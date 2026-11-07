"""Find the log time of the previous / next / nearest message of a topic, from the message indexes only.

Used by the cursor of the browser UI to snap to messages and to step through them with the arrow keys. Each lookup reads the
message index records of the few chunks around the time (16 bytes per message), never the message data, so it
stays fast for any file size. Files without chunk indexes give None (no snapping).
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from mcap.reader import make_reader

_OP_MESSAGE_INDEX = 0x07


class MessageTimes:
    """Message time lookups for one recording; keeps its summary (chunk index) in memory."""

    def __init__(self, path: Path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            self.summary = make_reader(f).get_summary()
        self.indexed = bool(self.summary and self.summary.chunk_indexes
                            and all(ci.message_index_offsets for ci in self.summary.chunk_indexes))
        self.channels: dict[str, set[int]] = {}
        if self.summary:
            for cid, ch in self.summary.channels.items():
                self.channels.setdefault(ch.topic, set()).add(cid)

    def _times(self, f, ci, ids: set[int]) -> np.ndarray:
        """Log times of the messages of channels ``ids`` in chunk ``ci``."""
        out = []
        for cid in ids & set(ci.message_index_offsets):
            f.seek(ci.message_index_offsets[cid])
            head = f.read(9 + 6)
            op, length = head[0], struct.unpack_from("<Q", head, 1)[0]
            if op != _OP_MESSAGE_INDEX:
                continue
            n = struct.unpack_from("<I", head, 11)[0]
            entries = np.frombuffer(f.read(n), dtype="<u8").reshape(-1, 2)
            out.append(entries[:, 0].astype(np.int64))
        return np.concatenate(out) if out else np.empty(0, np.int64)

    def step(self, topic: str, t: int, direction: int) -> int | None:
        """Log time of the message of ``topic`` before (-1), after (+1) or nearest to (0) ``t`` (ns), or None.

        Before / after are strict, so repeated steps move from message to message.
        """
        ids = self.channels.get(topic)
        if not self.indexed or not ids:
            return None
        chunks = [ci for ci in self.summary.chunk_indexes if ids & set(ci.message_index_offsets)]
        best_prev = best_next = None
        with open(self.path, "rb") as f:
            if direction >= 0:                 # earliest message > t (or >= t for nearest)
                for ci in sorted((c for c in chunks if c.message_end_time >= t), key=lambda c: c.message_start_time):
                    if best_next is not None and ci.message_start_time > best_next:
                        break                  # later chunks cannot hold an earlier message
                    ts = self._times(f, ci, ids)
                    ts = ts[ts >= t] if direction == 0 else ts[ts > t]
                    if ts.size:
                        best_next = int(ts.min()) if best_next is None else min(best_next, int(ts.min()))
            if direction <= 0:                 # latest message < t (or <= t for nearest)
                for ci in sorted((c for c in chunks if c.message_start_time <= t), key=lambda c: -c.message_end_time):
                    if best_prev is not None and ci.message_end_time < best_prev:
                        break
                    ts = self._times(f, ci, ids)
                    ts = ts[ts <= t] if direction == 0 else ts[ts < t]
                    if ts.size:
                        best_prev = int(ts.max()) if best_prev is None else max(best_prev, int(ts.max()))
        if direction > 0:
            return best_next
        if direction < 0:
            return best_prev
        cands = [x for x in (best_prev, best_next) if x is not None]
        return min(cands, key=lambda x: abs(x - t)) if cands else None

    def activity(self, topic: str, t0: int, t1: int, bins: int) -> list[int] | None:
        """Messages of ``topic`` per slice of [t0, t1] (ns) in ``bins`` slices, from the message indexes; None without.

        Used when the topic timelines of the browser UI are zoomed in: only chunks overlapping the range are looked at.
        """
        ids = self.channels.get(topic)
        if not self.indexed or not ids or t1 <= t0 or bins <= 0:
            return None
        counts = np.zeros(bins, np.int64)
        with open(self.path, "rb") as f:
            for ci in self.summary.chunk_indexes:
                if ci.message_end_time < t0 or ci.message_start_time > t1 or not ids & set(ci.message_index_offsets):
                    continue
                ts = self._times(f, ci, ids)
                ts = ts[(ts >= t0) & (ts <= t1)]
                if ts.size:
                    counts += np.bincount(np.clip((ts - t0) * bins // (t1 - t0 + 1), 0, bins - 1), minlength=bins)
        return counts.tolist()
