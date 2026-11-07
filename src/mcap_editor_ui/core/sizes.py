"""Size per topic from the message indexes, without decompressing any chunk.

After every chunk an indexed MCAP file has one message index per channel: (log time, byte offset inside the
uncompressed chunk) for every message. Sorting all offsets of a chunk and taking differences gives the size of
each message record, so the uncompressed size per topic is exact up to the record headers. Which part of the
compressed chunk belongs to which topic is not stored; it is estimated by giving each topic the chunk's
compression ratio. Only the indexes are read (16 bytes per message), so this is fast even for huge files.

The same indexes give the log time of every message, so each topic also gets an activity histogram: messages per
time slice (``ACTIVITY_BINS`` slices over the recording), shown as strips in the topic timelines of the browser UI to reveal gaps.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from mcap.reader import make_reader

from .bag import BagInfo

_UNITS = {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9}

ACTIVITY_BINS = 400                     # time slices of the activity histogram

_MSG_HEADER = 1 + 8 + 2 + 4 + 8 + 8     # opcode, record length, channel id, sequence, log time, publish time
_OP_MESSAGE_INDEX = 0x07


@dataclass
class TopicSize:
    """Size of one topic: ``raw`` = uncompressed message data, ``stored`` = estimated share of the file."""

    raw: int = 0
    stored: float = 0.0
    chunks: list[tuple[int, int, float]] = field(default_factory=list)   # (start ns, end ns, stored bytes) per chunk
    activity: np.ndarray = field(default_factory=lambda: np.zeros(ACTIVITY_BINS, np.int64))   # messages per slice


def read_sizes(path: Path, info: BagInfo, progress=None) -> dict[str, TopicSize] | None:
    """Size per topic, or None if the file has no message indexes (then only a full read could tell).

    ``progress(done)`` (fraction of the chunks, 0–1) is called per chunk; an exception raised there stops reading.
    """
    if not info.indexed:
        return None
    topic_of = {cid: t.topic for t in info.topics.values() for cid in t.channel_ids}
    sizes = {t: TopicSize() for t in info.topics}
    with open(path, "rb") as f:
        summary = make_reader(f).get_summary()
        n = len(summary.chunk_indexes)
        for i, ci in enumerate(summary.chunk_indexes):
            if progress and i % 20 == 0:
                progress(i / n)
            if not ci.message_index_offsets or not ci.message_index_length:
                return None
            f.seek(ci.chunk_start_offset + ci.chunk_length)
            buf = f.read(ci.message_index_length)
            offsets, channels, times = _parse_message_indexes(buf)
            if offsets.size == 0:
                continue
            order = np.argsort(offsets, kind="stable")
            offsets, channels, times = offsets[order], channels[order], times[order]
            span = max(info.end_ns - info.start_ns, 1)
            bins = np.clip((times - info.start_ns) * ACTIVITY_BINS // (span + 1), 0, ACTIVITY_BINS - 1)
            lengths = np.diff(offsets, append=ci.uncompressed_size) - _MSG_HEADER
            ratio = ci.compressed_size / ci.uncompressed_size if ci.uncompressed_size else 1.0
            for cid in np.unique(channels):
                mine = channels == cid
                raw = int(lengths[mine].sum())
                ts = sizes[topic_of[int(cid)]]
                ts.activity += np.bincount(bins[mine], minlength=ACTIVITY_BINS)
                ts.raw += raw
                ts.stored += raw * ratio
                ts.chunks.append((ci.message_start_time, ci.message_end_time, raw * ratio))
    return sizes


def _parse_message_indexes(buf: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Offsets, channel ids and log times of all entries in the message index records of one chunk."""
    offs, chans, times = [], [], []
    pos = 0
    while pos + 9 <= len(buf):
        op, length = buf[pos], struct.unpack_from("<Q", buf, pos + 1)[0]
        body = pos + 9
        if op == _OP_MESSAGE_INDEX:
            cid, n = struct.unpack_from("<HI", buf, body)
            entries = np.frombuffer(buf, dtype="<u8", count=n // 8, offset=body + 6).reshape(-1, 2)
            offs.append(entries[:, 1])
            times.append(entries[:, 0])
            chans.append(np.full(len(entries), cid, dtype=np.uint16))
        pos = body + length
    if not offs:
        return np.empty(0, np.int64), np.empty(0, np.uint16), np.empty(0, np.int64)
    return np.concatenate(offs).astype(np.int64), np.concatenate(chans), np.concatenate(times).astype(np.int64)


def estimate_output(sizes: dict[str, TopicSize], topics: set[str], t0: int, t1: int) -> float:
    """Estimated output size in bytes for ``topics`` in the window [t0, t1] (messages spread evenly in each chunk)."""
    total = 0.0
    for t in topics:
        for c0, c1, stored in sizes[t].chunks:
            if c1 < t0 or c0 > t1:
                continue
            span = c1 - c0
            total += stored if span <= 0 else stored * (min(c1, t1) - max(c0, t0)) / span
    return total


def table_unit(file_size: int, setting: str | None = None) -> str:
    """Unit for a table of sizes of a file of ``file_size`` bytes, following ``setting`` (default: setting sizes.unit).

    "kB", "MB", "GB" are fixed, "auto" lets each value pick its unit, "smart" picks one unit for the whole table
    from the file size: < 1 GB → kB, < 100 GB → MB, else GB.
    """
    if setting is None:
        from . import settings
        setting = settings.get("sizes.unit")
    if setting in ("kB", "MB", "GB", "auto"):
        return setting
    if setting != "smart":
        raise ValueError(f"size unit must be kB, MB, GB, auto or smart, not {setting!r}")
    return "kB" if file_size < 1e9 else "MB" if file_size < 100e9 else "GB"


def fmt_bytes(n: float | None, unit: str = "auto") -> str:
    """Size in SI units with 2 decimals; ``unit`` "auto" picks one per value.

    A size that is not zero but rounds to 0.00 is shown as "~0.00", to tell it from a topic without data.
    """
    if n is None:
        return "?"
    if unit == "auto":
        unit = next((u for u in ("GB", "MB", "kB") if n >= _UNITS[u]), "B")
    v = n / _UNITS[unit]
    text = f"{v:.2f} {unit}" if unit != "B" else f"{n:.0f} B"
    return ("~" + text) if n > 0 and round(v, 2) == 0 and unit != "B" else text


def fmt_share(part: float, total: float) -> str:
    """Percentage with 2 decimals; "~0.00%" for a share that is not zero but rounds to it."""
    v = 100 * part / total if total else 0.0
    return f"~{v:.2f}%" if part > 0 and round(v, 2) == 0 else f"{v:.2f}%"
