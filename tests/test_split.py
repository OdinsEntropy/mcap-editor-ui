"""Tests of compression choice, splitting and the QoS depth of latched topics."""

from __future__ import annotations

import pytest
from mcap.reader import make_reader
from mcap.records import Channel
from mcap.writer import CompressionType

from conftest import QOS_JAZZY_LATCHED, T_BASE, expected_times, write_bag
from mcap_editor_ui.core.bag import latched_depth
from mcap_editor_ui.core.edit import edit, parse_split, part_path


def messages(path):
    """(topic, log time) of all messages in ``path``, in file order."""
    with open(path, "rb") as f:
        return [(ch.topic, m.log_time) for _, ch, m in make_reader(f).iter_messages(log_time_order=False)]


def compression_of(path) -> set[str]:
    """Chunk compressions used in ``path``."""
    with open(path, "rb") as f:
        return {c.compression for c in make_reader(f).get_summary().chunk_indexes}


def ch(qos: str) -> Channel:
    """Channel with the given offered_qos_profiles."""
    return Channel(id=1, topic="/x", message_encoding="cdr", metadata={"offered_qos_profiles": qos}, schema_id=0)


def test_latched_depth_from_qos():
    assert latched_depth(ch(QOS_JAZZY_LATCHED)) == 1
    two = QOS_JAZZY_LATCHED + QOS_JAZZY_LATCHED.replace("depth: 1", "depth: 5")     # two publishers
    assert latched_depth(ch(two)) == 6
    assert latched_depth(ch("- history: keep_all\n  depth: 0\n  durability: transient_local\n")) is None
    assert latched_depth(ch("- history: 3\n  depth: 0\n  durability: 1\n")) is None       # older rosbag2, unknown
    assert latched_depth(Channel(id=1, topic="/x", message_encoding="cdr", metadata={"latching": "1"}, schema_id=0)) is None


def test_only_last_depth_latched_messages(tmp_path):
    """/b treated as latched without QoS → all earlier ones; with the conftest QoS /tf_static depth 1 → 1."""
    src = write_bag(tmp_path / "rec.mcap")
    res = edit(src, tmp_path / "o.mcap", {"/b", "/tf_static"}, start_s=5.5, extra_latched={"/b"})
    assert res.latched == {"/b": 6, "/tf_static": 1}           # /b: 0 … 5 s, unknown depth → all


@pytest.mark.parametrize("comp, expect", [("none", ""), ("lz4", "lz4"), ("zstd", "zstd"), ("keep", "zstd")])
def test_compression_choice(tmp_path, comp, expect):
    src = write_bag(tmp_path / "rec.mcap")
    res = edit(src, tmp_path / "o.mcap", {"/a"}, compression=comp)
    assert compression_of(res.out) == {expect}


def test_split_by_duration(tmp_path):
    src = write_bag(tmp_path / "rec.mcap")
    out = tmp_path / "o.mcap"
    res = edit(src, out, {"/a", "/tf_static"}, start_s=1.0, end_s=7.0, split=parse_split("duration", 2.5))
    assert res.outputs == [part_path(out, k) for k in range(3)] and not out.exists()
    parts = [messages(p) for p in res.outputs]
    bounds = [T_BASE + round(s * 1e9) for s in (1.0, 3.5, 6.0, 7.0)]
    for k, msgs in enumerate(parts):
        a = [t for topic, t in msgs if topic == "/a"]
        assert a == [t for t in expected_times("/a") if bounds[k] <= t < bounds[k + 1] or (k == 2 and t == bounds[3])]
        tf = [t for topic, t in msgs if topic == "/tf_static"]
        assert tf == [bounds[k]]                                # latched message again at the start of every part
    assert sum(res.kept.values()) == 61 and res.latched == {"/tf_static": 3}


def test_split_by_size(tmp_path):
    src = write_bag(tmp_path / "rec.mcap")
    out = tmp_path / "o.mcap"
    res = edit(src, out, {"/a", "/c", "/tf_static"}, start_s=1.0, split=("size", 20_000), compression="none")
    assert len(res.outputs) >= 3
    all_a = [t for p in res.outputs for topic, t in messages(p) if topic == "/a"]
    assert all_a == [t for t in expected_times("/a") if t >= T_BASE + 10**9]      # nothing lost or doubled
    for p in res.outputs:
        assert ("/tf_static" in {topic for topic, _ in messages(p)})


def test_split_refuses_existing_part_and_cleans_up(tmp_path):
    src = write_bag(tmp_path / "rec.mcap")
    out = tmp_path / "o.mcap"
    part_path(out, 1).write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        edit(src, out, {"/a"}, split=parse_split("duration", 2.0))
    assert part_path(out, 1).read_bytes() == b"keep me"
    assert not part_path(out, 0).exists() and not list(tmp_path.glob("*.part"))   # part 0 of this run removed


def test_parse_split_errors():
    assert parse_split("off") is None and parse_split("size", size_gb=0.5) == ("size", 0.5e9)
    for bad in [("duration", 0), ("size", None), ("weekly", 1)]:
        with pytest.raises(ValueError):
            parse_split(bad[0], bad[1], bad[1])


def test_lz4_input_kept(tmp_path):
    src = write_bag(tmp_path / "rec.mcap", compression=CompressionType.LZ4)
    assert compression_of(edit(src, tmp_path / "o.mcap", {"/a"}).out) == {"lz4"}


def test_split_latched_only_in_first_part(tmp_path):
    src = write_bag(tmp_path / "rec.mcap")
    res = edit(src, tmp_path / "o.mcap", {"/a", "/tf_static"}, start_s=1.0, end_s=7.0,
               split=parse_split("duration", 2.5), split_repeat_latched=False)
    tf = [[t for topic, t in messages(p) if topic == "/tf_static"] for p in res.outputs]
    assert tf == [[T_BASE + 10**9], [], []] and res.latched == {"/tf_static": 1}
