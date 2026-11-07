"""Synthetic MCAP recordings with known content for the tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcap.writer import CompressionType, IndexType, Writer

T_BASE = 1_700_000_000 * 10**9           # absolute log time of the first message (ns)
DURATION_S = 10.0

QOS_JAZZY_LATCHED = """- history: keep_last
  depth: 1
  reliability: reliable
  durability: transient_local
  deadline:
    sec: 9223372036
    nsec: 854775807
  lifespan:
    sec: 9223372036
    nsec: 854775807
  liveliness: automatic
  liveliness_lease_duration:
    sec: 9223372036
    nsec: 854775807
  avoid_ros_namespace_conventions: false
"""
QOS_HUMBLE_LATCHED = "- history: 3\n  depth: 0\n  reliability: 1\n  durability: 1\n"
QOS_VOLATILE = "- history: keep_last\n  depth: 10\n  reliability: reliable\n  durability: volatile\n"

# topic → (rate in Hz or None for one message, first message in s, channel metadata)
TOPICS = {
    "/a": (10.0, 0.0, {"offered_qos_profiles": QOS_VOLATILE}),        # 101 messages at 0.0, 0.1, … 10.0 s
    "/b": (1.0, 0.0, {}),                                             # 11 messages
    "/c": (10.0, 0.05, {"offered_qos_profiles": QOS_VOLATILE}),       # 100 messages at 0.05 … 9.95 s
    "/tf_static": (None, 0.0, {"offered_qos_profiles": QOS_JAZZY_LATCHED}),
    "/robot_description": (None, 0.01, {"offered_qos_profiles": QOS_HUMBLE_LATCHED}),
    "/ros1_latched": (None, 0.02, {"latching": "1"}),
}


def payload(topic: str, t_ns: int) -> bytes:
    """Message content that identifies topic and original log time."""
    return f"{topic}@{t_ns}".encode() + b"x" * 200


def expected_times(topic: str) -> list[int]:
    """Absolute log times (ns) of all messages of ``topic`` in the synthetic recording."""
    rate, first, _ = TOPICS[topic]
    if rate is None:
        return [T_BASE + round(first * 1e9)]
    n = int(round((DURATION_S - first) * rate)) + 1
    return [T_BASE + round((first + i / rate) * 1e9) for i in range(n) if first + i / rate <= DURATION_S + 1e-9]


def write_bag(path: Path, compression=CompressionType.ZSTD, indexed: bool = True, chunked: bool = True) -> Path:
    """Write the synthetic recording; small chunks so that time cuts can skip some of them."""
    with open(path, "wb") as f:
        kw = {} if indexed else dict(index_types=IndexType.NONE, use_statistics=False, repeat_channels=False,
                                     repeat_schemas=False, use_summary_offsets=False)
        w = Writer(f, chunk_size=2048, compression=compression, use_chunking=chunked, **kw)
        w.start(profile="ros2", library="test")
        sid = w.register_schema("std_msgs/msg/String", "ros2msg", b"string data")
        ids = {t: w.register_channel(t, "cdr", sid, md) for t, (_, _, md) in TOPICS.items()}
        msgs = sorted((t_ns, topic) for topic in TOPICS for t_ns in expected_times(topic))
        for seq, (t_ns, topic) in enumerate(msgs):
            w.add_message(ids[topic], log_time=t_ns, data=payload(topic, t_ns), publish_time=t_ns + 1, sequence=seq)
        w.add_metadata("rosbag2", {"note": "synthetic"})
        w.add_attachment(T_BASE, T_BASE, "calib.yaml", "text/yaml", b"k: 1\n")
        w.finish()
    return path


@pytest.fixture
def bag(tmp_path) -> Path:
    """Indexed, zstd-compressed synthetic recording."""
    return write_bag(tmp_path / "rec.mcap")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Every test gets an empty user settings folder, so the user's real settings never influence a test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
