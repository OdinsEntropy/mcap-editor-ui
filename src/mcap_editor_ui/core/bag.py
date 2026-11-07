"""Read the overview of an MCAP file (topics, counts, time range, latched flags) without reading its messages.

The overview comes from the summary section at the end of the file, so it costs a few kB of reads even for
huge recordings. Files without a summary (e.g. a recording that was killed) are scanned once from front to back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from mcap.reader import make_reader
from mcap.records import Channel, Message, Schema
from mcap.stream_reader import StreamReader

# ROS 2 durability enum as written by older rosbag2 versions (rmw_qos_durability_policy_t); newer ones write strings
_TRANSIENT_LOCAL = {1, "transient_local"}


@dataclass
class TopicInfo:
    """One topic of the recording (several channels with the same topic are merged)."""

    topic: str
    schema: str
    encoding: str
    count: int | None          # None if the file has no statistics
    latched: bool              # transient_local (ROS 2) or latching (ROS 1) publisher
    channel_ids: list[int] = field(default_factory=list)


@dataclass
class BagInfo:
    """Overview of an MCAP file; times in ns since the epoch, as in MCAP."""

    path: Path
    size: int                  # bytes
    start_ns: int              # log time of the first message
    end_ns: int                # log time of the last message
    topics: dict[str, TopicInfo]
    compression: str           # chunk compression ("zstd", "lz4" or "")
    indexed: bool              # summary with chunk indexes present → time cuts can skip chunks
    chunk_size: int = 1024 * 1024  # typical uncompressed chunk size in bytes, reused for the output

    @property
    def duration_s(self) -> float:
        """Time between first and last message in s."""
        return (self.end_ns - self.start_ns) / 1e9


def channel_is_latched(channel: Channel) -> bool:
    """Whether the publisher of ``channel`` re-sends its last messages to late subscribers.

    ROS 2 (rosbag2): ``offered_qos_profiles`` is a YAML list of QoS profiles; latched if any has durability
    transient_local. ROS 1: ``latching`` is "1". Other channels carry no such information and count as not latched.
    """
    md = channel.metadata or {}
    if str(md.get("latching", "")).lower() in ("1", "true"):
        return True
    qos = md.get("offered_qos_profiles")
    if not qos:
        return False
    try:
        profiles = yaml.safe_load(qos)
    except yaml.YAMLError:
        return "transient_local" in qos
    if isinstance(profiles, dict):
        profiles = [profiles]
    for p in profiles or []:
        if isinstance(p, dict):
            d = p.get("durability")
            if (d.lower() if isinstance(d, str) else d) in _TRANSIENT_LOCAL:
                return True
    return False


def latched_depth(channel: Channel) -> int | None:
    """How many of its last messages a latched publisher re-sends to late subscribers; None if unknown or all.

    ROS 2: sum of ``depth`` over the transient_local profiles with history keep_last (one profile per publisher).
    keep_all, system default or unknown history → None. ROS 1 latching (last message per publisher, number of
    publishers unknown) → None. Callers then keep every message, which copies too much rather than too little.
    """
    qos = (channel.metadata or {}).get("offered_qos_profiles")
    if not qos:
        return None
    try:
        profiles = yaml.safe_load(qos)
    except yaml.YAMLError:
        return None
    if isinstance(profiles, dict):
        profiles = [profiles]
    total = 0
    for p in profiles or []:
        if not isinstance(p, dict):
            continue
        d = p.get("durability")
        if (d.lower() if isinstance(d, str) else d) not in _TRANSIENT_LOCAL:
            continue
        h = p.get("history")
        h = h.lower() if isinstance(h, str) else h
        depth = p.get("depth")
        if h not in ("keep_last", 1) or not isinstance(depth, int) or depth <= 0:
            return None
        total += depth
    return total or None


def _add_channel(topics: dict[str, TopicInfo], channel: Channel, schema_name: str, count: int | None) -> None:
    """Merge one channel into the per-topic overview."""
    t = topics.get(channel.topic)
    if t is None:
        topics[channel.topic] = TopicInfo(channel.topic, schema_name, channel.message_encoding, count,
                                          channel_is_latched(channel), [channel.id])
        return
    t.channel_ids.append(channel.id)
    t.latched = t.latched or channel_is_latched(channel)
    t.count = None if t.count is None or count is None else t.count + count


def read_info(path: Path, progress=None) -> BagInfo:
    """Overview of ``path`` from its summary, or from one full scan if the file has none.

    ``progress(done)`` (fraction 0–1) is called during a full scan; an exception raised there stops the scan.
    """
    path = Path(path)
    with open(path, "rb") as f:
        summary = make_reader(f).get_summary()
    if summary is not None and summary.channels:
        return _info_from_summary(path, summary)
    return _info_from_scan(path, progress)


def _info_from_summary(path: Path, summary) -> BagInfo:
    """Overview from the summary section (fast path)."""
    stats = summary.statistics
    counts = stats.channel_message_counts if stats else None
    topics: dict[str, TopicInfo] = {}
    for cid, ch in sorted(summary.channels.items()):
        schema = summary.schemas.get(ch.schema_id)
        _add_channel(topics, ch, schema.name if schema else "", counts.get(cid, 0) if counts is not None else None)
    if stats and stats.message_count:
        start, end = stats.message_start_time, stats.message_end_time
    elif summary.chunk_indexes:
        start = min(c.message_start_time for c in summary.chunk_indexes)
        end = max(c.message_end_time for c in summary.chunk_indexes)
    else:
        start = end = 0
    kinds = {c.compression for c in summary.chunk_indexes}
    sizes = sorted(c.uncompressed_size for c in summary.chunk_indexes)
    # median, so that the small last chunk does not count; at most 64 MiB to keep memory bounded
    chunk_size = min(max(sizes[len(sizes) // 2], 64 * 1024), 64 * 1024 * 1024) if sizes else 1024 * 1024
    return BagInfo(path, path.stat().st_size, start, end, topics,
                   kinds.pop() if len(kinds) == 1 else "zstd", bool(summary.chunk_indexes), chunk_size)


def _info_from_scan(path: Path, progress=None) -> BagInfo:
    """Overview from reading every record once (no summary in the file); memory stays at one chunk."""
    size = max(path.stat().st_size, 1)
    topics: dict[str, TopicInfo] = {}
    channels: dict[int, Channel] = {}
    schema_names: dict[int, str] = {}
    counts: dict[int, int] = {}
    start, end = None, None
    with open(path, "rb") as f:
        for n, record in enumerate(StreamReader(f).records):
            if progress and n % 2000 == 0:
                progress(f.tell() / size)
            if isinstance(record, Message):
                counts[record.channel_id] = counts.get(record.channel_id, 0) + 1
                start = record.log_time if start is None else min(start, record.log_time)
                end = record.log_time if end is None else max(end, record.log_time)
            elif isinstance(record, Channel):
                channels.setdefault(record.id, record)
            elif isinstance(record, Schema):
                schema_names.setdefault(record.id, record.name)
    for cid, ch in sorted(channels.items()):
        _add_channel(topics, ch, schema_names.get(ch.schema_id, ""), counts.get(cid, 0))
    return BagInfo(path, path.stat().st_size, start or 0, end or 0, topics, "zstd", False)
