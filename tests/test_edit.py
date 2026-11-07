"""Tests of reading the overview and of topic/time edits on synthetic recordings with known content."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcap.reader import make_reader
from mcap.writer import CompressionType

from conftest import DURATION_S, T_BASE, TOPICS, expected_times, payload, write_bag
from mcap_editor_ui.core.bag import read_info
from mcap_editor_ui.core.edit import edit

ALL = set(TOPICS)
LATCHED = {"/tf_static", "/robot_description", "/ros1_latched"}


def read_all(path: Path) -> dict[str, list[tuple[int, int, bytes]]]:
    """(log time, publish time, data) per topic of an output file, read with the plain mcap reader."""
    out: dict[str, list] = {}
    with open(path, "rb") as f:
        for _, ch, m in make_reader(f).iter_messages(log_time_order=False):
            out.setdefault(ch.topic, []).append((m.log_time, m.publish_time, m.data))
    return out


def window(topic: str, s0: float, s1: float) -> list[int]:
    """Expected absolute log times of ``topic`` inside [s0, s1] (s after the first message)."""
    return [t for t in expected_times(topic) if T_BASE + round(s0 * 1e9) <= t <= T_BASE + round(s1 * 1e9)]


def test_info(bag):
    info = read_info(bag)
    assert info.indexed and info.compression == "zstd"
    assert info.start_ns == T_BASE and info.duration_s == pytest.approx(DURATION_S)
    assert {t: ti.count for t, ti in info.topics.items()} == {t: len(expected_times(t)) for t in TOPICS}
    assert {t for t, ti in info.topics.items() if ti.latched} == LATCHED


def test_full_copy_is_identical(bag, tmp_path):
    res = edit(bag, tmp_path / "out.mcap", ALL)
    got = read_all(res.out)
    for topic in TOPICS:
        assert [(t, t + 1, payload(topic, t)) for t in expected_times(topic)] == got[topic]
    assert sum(res.latched.values()) == 0


def test_drop_and_keep(bag, tmp_path):
    res = edit(bag, tmp_path / "keep.mcap", {"/a", "/tf_static"})
    assert set(read_all(res.out)) == {"/a", "/tf_static"}
    with open(res.out, "rb") as f:   # channels of dropped topics are gone from the file
        assert {c.topic for c in make_reader(f).get_summary().channels.values()} == {"/a", "/tf_static"}


def test_time_window_inclusive_and_latched(bag, tmp_path):
    res = edit(bag, tmp_path / "cut.mcap", ALL, start_s=2.0, end_s=5.0)
    got = read_all(res.out)
    t0 = T_BASE + 2 * 10**9
    assert [m[0] for m in got["/a"]] == window("/a", 2.0, 5.0) and len(got["/a"]) == 31   # 2.0 and 5.0 included
    assert [m[0] for m in got["/c"]] == window("/c", 2.0, 5.0)
    for topic in LATCHED:                              # one message each, moved to the window start, data unchanged
        (lt, pt, data), = got[topic]
        orig = expected_times(topic)[0]
        assert lt == t0 and pt == orig + 1 and data == payload(topic, orig)
    assert res.latched == dict.fromkeys(sorted(LATCHED), 1)
    with open(res.out, "rb") as f:
        st = make_reader(f).get_summary().statistics
    assert st.message_start_time == t0 and st.message_end_time == T_BASE + 5 * 10**9


def test_latched_only_if_topic_kept(bag, tmp_path):
    res = edit(bag, tmp_path / "cut.mcap", {"/a", "/tf_static"}, start_s=2.0)
    assert set(read_all(res.out)) == {"/a", "/tf_static"}
    assert res.latched == {"/tf_static": 1}


def test_no_latched(bag, tmp_path):
    res = edit(bag, tmp_path / "cut.mcap", ALL, start_s=2.0, keep_latched=False)
    assert not LATCHED & set(read_all(res.out))


def test_extra_latched(bag, tmp_path):
    res = edit(bag, tmp_path / "cut.mcap", {"/b"}, start_s=2.5, end_s=4.5, extra_latched={"/b"})
    assert res.latched == {"/b": 3}                     # all /b messages before the start: 0, 1, 2 s
    assert res.kept == {"/b": 2}                        # 3 s and 4 s


def test_end_beyond_recording_means_until_end(bag, tmp_path):
    res = edit(bag, tmp_path / "cut.mcap", {"/a"}, start_s=9.0, end_s=1000.0)
    assert [m[0] for m in read_all(res.out)["/a"]] == window("/a", 9.0, DURATION_S)


def test_small_window_reads_only_some_chunks(bag, tmp_path):
    res = edit(bag, tmp_path / "cut.mcap", ALL, start_s=4.0, end_s=5.0, keep_latched=False)
    assert res.read_bytes < 0.2 * bag.stat().st_size


@pytest.mark.parametrize("kind", ["no_index", "no_chunks"])
def test_unindexed_input_gives_same_result(tmp_path, kind):
    ref = write_bag(tmp_path / "ref.mcap")
    src = write_bag(tmp_path / f"{kind}.mcap", indexed=kind != "no_index", chunked=kind != "no_chunks")
    assert not read_info(src).indexed
    a = edit(ref, tmp_path / "a.mcap", {"/a", "/c", "/tf_static"}, start_s=1.5, end_s=3.0)
    b = edit(src, tmp_path / "b.mcap", {"/a", "/c", "/tf_static"}, start_s=1.5, end_s=3.0)
    assert read_all(a.out) == read_all(b.out)
    assert (a.kept, a.latched) == (b.kept, b.latched)


def test_metadata_attachments_and_compression(tmp_path):
    src = write_bag(tmp_path / "lz4.mcap", compression=CompressionType.LZ4)
    res = edit(src, tmp_path / "out.mcap", {"/b"}, start_s=1.0)
    with open(res.out, "rb") as f:
        r = make_reader(f)
        assert [m.name for m in r.iter_metadata()] == ["rosbag2"]
        assert [(a.name, a.data) for a in r.iter_attachments()] == [("calib.yaml", b"k: 1\n")]
        assert {c.compression for c in r.get_summary().chunk_indexes} == {"lz4"}
        assert r.get_header().profile == "ros2"


def test_errors(bag, tmp_path):
    existing = tmp_path / "exists.mcap"
    existing.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        edit(bag, existing, ALL)
    assert existing.read_bytes() == b"keep me"
    with pytest.raises(FileExistsError):
        edit(bag, bag, ALL)
    with pytest.raises(ValueError, match="not in"):
        edit(bag, tmp_path / "o.mcap", {"/nope"})
    with pytest.raises(ValueError, match="after the end"):
        edit(bag, tmp_path / "o.mcap", ALL, start_s=11.0)
    with pytest.raises(ValueError, match="not after start"):
        edit(bag, tmp_path / "o.mcap", ALL, start_s=3.0, end_s=3.0)
    assert not list(tmp_path.glob("o.mcap*"))          # no output and no leftover .part


def test_latched_off_per_topic(bag, tmp_path):
    res = edit(bag, tmp_path / "cut.mcap", ALL, start_s=2.0, latched_off={"/robot_description"})
    got = read_all(res.out)
    assert "/robot_description" not in got                   # no earlier message, and none in the window
    assert res.latched == {"/ros1_latched": 1, "/tf_static": 1}
