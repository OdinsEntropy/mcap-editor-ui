"""Write a copy of an MCAP file with only some topics and/or only a time window.

Memory stays at about one chunk (a few MB) whatever the file size:

* Indexed files (summary with chunk indexes, the normal case): the chunk index at the end of the file says where
  each chunk lies, which time range it covers and which channels it contains. Only chunks that overlap the time
  window and contain a wanted channel are read and decompressed, one at a time. A 2–20 s cut of a long recording
  therefore reads only those seconds of data.
* Files without a summary (e.g. a killed recorder): read once from front to back with the StreamReader.

Messages are written in the order of the input file. The output uses the input's chunk size (a different size
changes how well chunks compress) and its compression unless another one is chosen. Metadata goes into every
output file, attachments into one (the first, for indexed input). The input is never changed, an existing output is never overwritten,
and each file is written to ``<name>.part`` and renamed when complete; on an error or cancel all files of the run
are removed.

Latched topics (ROS 2 transient_local, ROS 1 latching, e.g. /tf_static, /robot_description) are often published
only once at the start of a recording. With ``keep_latched`` their last messages from before the window start are
copied as well and get the window start as log time, so the cut file starts at the cut and still contains them
(publish time and message content stay unchanged). "Last messages" = as many as the publisher's QoS depth (see
``bag.latched_depth``), all if unknown.

Splitting (as ``ros2 bag record --max-bag-duration / --max-bag-size`` does, numbered ``<out>_0.mcap``, ``_1`` …):
by duration, part k holds the messages from window start + k·duration on; by size, a new part starts when the
current file reaches the size (checked after each written chunk, so parts end slightly above it). Every part
starts with the latched messages again, so each part opens correctly on its own (``split_repeat_latched``;
off: only part 0, so joined parts have no duplicates). Parts follow the input file
order, so with overlapping input chunks a message can land a few ms on the other side of a duration boundary.
"""

from __future__ import annotations

import io
import sys
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mcap.data_stream import ReadDataStream
from mcap.reader import make_reader
from mcap.records import Attachment, Channel, Chunk, Header, Message, Metadata, Schema
from mcap.stream_reader import StreamReader, breakup_chunk
from mcap.writer import CompressionType, Writer

from .bag import BagInfo, latched_depth, read_info

_COMPRESSION = {"zstd": CompressionType.ZSTD, "lz4": CompressionType.LZ4, "": CompressionType.NONE,
                "none": CompressionType.NONE}
COMPRESSIONS = ("keep", "zstd", "lz4", "none")
LIBRARY = "mcap_editor_ui"


@dataclass
class EditResult:
    """What was written: messages per topic inside the window, latched messages copied from before it."""

    out: Path                  # requested output; with splitting the files are in ``outputs``
    kept: dict[str, int] = field(default_factory=dict)
    latched: dict[str, int] = field(default_factory=dict)
    start_ns: int = 0          # absolute window start (log time)
    end_ns: int = 0            # absolute window end (log time, inclusive)
    read_bytes: int = 0        # compressed bytes read from the input (chunks or whole file)
    out_size: int = 0          # all output files together
    seconds: float = 0.0
    outputs: list[Path] = field(default_factory=list)


class Progress:
    """Reports progress (fraction 0–1 of the work) with an estimate of the time left.

    On a terminal one line is updated in place (at most 5× per s); otherwise (log file, pipe) a new line is
    printed every ``step``. Call ``finish()`` at the end to close the in-place line.
    """

    def __init__(self, log: Callable[[str], None] = print, step: float = 0.05, inline: bool | None = None):
        self.log, self.step = log, step
        self.inline = sys.stdout.isatty() if inline is None else inline
        self.t0, self.next, self.last = time.monotonic(), step, 0.0
        self.open = False

    def __call__(self, done: float, written: int) -> None:
        """Report that fraction ``done`` of the work is finished and ``written`` messages were written."""
        now = time.monotonic()
        if done <= 0 or (self.inline and now - self.last < 0.2 and done < 1) or (not self.inline and done < self.next):
            return
        elapsed = now - self.t0
        line = f"  {done:4.0%}  {written} messages written, {elapsed:.0f} s, ~{elapsed / done * (1 - done):.0f} s left"
        if self.inline:
            sys.stdout.write("\r" + line + "\x1b[K")
            sys.stdout.flush()
            self.last, self.open = now, True
            return
        self.log(line)
        while self.next <= done:
            self.next += self.step

    def finish(self) -> None:
        """End the in-place progress line (no-op otherwise)."""
        if self.open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.open = False


def window_ns(info: BagInfo, start_s: float = 0.0, end_s: float | None = None) -> tuple[int, int]:
    """Absolute [start, end] log times (ns, inclusive) for a window given in s after the first message.

    An end beyond the last message (or None) means "until the end". Raises ValueError for an empty window.
    """
    if start_s < 0:
        raise ValueError(f"start {start_s} s is negative")
    if end_s is not None and end_s <= start_s:
        raise ValueError(f"end {end_s} s is not after start {start_s} s")
    t0 = info.start_ns + round(start_s * 1e9)
    if t0 > info.end_ns:
        raise ValueError(f"start {start_s} s is after the end of the recording ({info.duration_s:.3f} s)")
    t1 = info.end_ns if end_s is None else min(info.end_ns, info.start_ns + round(end_s * 1e9))
    return t0, t1


def part_path(out: Path, k: int) -> Path:
    """Name of part ``k`` of a split output: ``<stem>_<k>.mcap``, as rosbag2 numbers its split files."""
    return out.with_name(f"{out.stem}_{k}{out.suffix}")


def parse_split(mode: str | None, duration_s: float | None = None, size_gb: float | None = None) -> tuple[str, float] | None:
    """("duration", ns) or ("size", bytes) from a split mode and its value; None for no split."""
    if mode in (None, "", "off"):
        return None
    if mode == "duration":
        if not duration_s or duration_s <= 0:
            raise ValueError("split duration must be > 0 s")
        return ("duration", duration_s * 1e9)
    if mode == "size":
        if not size_gb or size_gb <= 0:
            raise ValueError("split size must be > 0 GB")
        return ("size", size_gb * 1e9)
    raise ValueError(f"split must be off, duration or size, not {mode!r}")


def edit(src: Path, out: Path, topics: Iterable[str], start_s: float = 0.0, end_s: float | None = None,
         keep_latched: bool = True, extra_latched: Iterable[str] = (), latched_off: Iterable[str] = (),
         progress: Callable[[float, int], None] | None = None, info: BagInfo | None = None,
         compression: str = "keep", split: tuple[str, float] | None = None,
         split_repeat_latched: bool = True) -> EditResult:
    """Copy the messages of ``topics`` between ``start_s`` and ``end_s`` (s after the first message) to ``out``.

    ``extra_latched`` names topics to treat as latched even if the file has no QoS information for them;
    ``latched_off`` names latched topics whose earlier messages are not included (their window messages still are).
    ``compression``: "keep" (as the input), "zstd", "lz4" or "none". ``split``: None, or from ``parse_split``.
    ``split_repeat_latched``: every part starts with the latched messages (each part opens correctly on its own);
    False writes them only into part 0 (the parts can be joined again without duplicates).
    Returns counts per topic. Raises FileExistsError if an output exists or is the input, ValueError for unknown
    topics, an empty time window or invalid options.
    """
    src, out = Path(src), Path(out)
    first = part_path(out, 0) if split else out
    if first.exists() or first.resolve() == src.resolve():
        raise FileExistsError(f"{first} exists already (or is the input); choose a new output file")
    if compression not in COMPRESSIONS:
        raise ValueError(f"compression must be one of {', '.join(COMPRESSIONS)}")
    info = info or read_info(src)
    topics = set(topics)
    unknown = topics - set(info.topics)
    if unknown:
        raise ValueError(f"not in {src.name}: {', '.join(sorted(unknown))}")
    t0, t1 = window_ns(info, start_s, end_s)
    extra = set(extra_latched)
    off = set(latched_off)
    latched = {t for t in topics if (info.topics[t].latched or t in extra) and t not in off} if keep_latched else set()

    res = EditResult(out, kept=dict.fromkeys(sorted(topics), 0), latched=dict.fromkeys(sorted(latched), 0),
                     start_ns=t0, end_ns=t1)
    progress = progress or (lambda done, written: None)
    comp = info.compression if compression == "keep" else compression
    chunk_size = info.chunk_size
    if split and split[0] == "size":           # a part can only end after a chunk: keep chunks well below the part size
        chunk_size = max(1024, min(chunk_size, int(split[1] // 8)))
    t_start = time.monotonic()
    with open(src, "rb") as fin:
        output = _Output(out, src, _COMPRESSION.get(comp, CompressionType.ZSTD), chunk_size, split, t0, res,
                         repeat_latched=split_repeat_latched)
        try:
            copy = _copy_indexed if info.indexed else _copy_stream
            copy(fin, output, topics, latched, t0, t1, progress, info.size)
            output.finish()
        except BaseException:
            output.abort()                     # our own incomplete outputs, never user data
            raise
    res.out_size = sum(p.stat().st_size for p in res.outputs)
    res.seconds = time.monotonic() - t_start
    return res


class _Channels:
    """Maps input channel/schema ids to output ids of one writer; registers each schema and channel once."""

    def __init__(self, writer: Writer):
        self.writer = writer
        self.schema_ids: dict[int, int] = {}
        self.channel_ids: dict[int, int] = {}

    def add(self, channel: Channel, schema: Schema | None) -> int:
        """Output channel id for ``channel`` (registered on first use)."""
        if channel.id in self.channel_ids:
            return self.channel_ids[channel.id]
        sid = 0
        if schema is not None and channel.schema_id:
            if schema.id not in self.schema_ids:
                self.schema_ids[schema.id] = self.writer.register_schema(schema.name, schema.encoding, schema.data)
            sid = self.schema_ids[schema.id]
        self.channel_ids[channel.id] = self.writer.register_channel(channel.topic, channel.message_encoding, sid,
                                                                    dict(channel.metadata))
        return self.channel_ids[channel.id]


class _Output:
    """The output file(s) of one edit: opens parts, rolls over when splitting, keeps the latched messages.

    ``store`` holds, per latched input channel, its last messages (as many as the QoS depth); they are written at
    the start of every file with that file's start as log time.
    """

    def __init__(self, out: Path, src: Path, compression: CompressionType, chunk_size: int,
                 split: tuple[str, float] | None, t0: int, res: EditResult, repeat_latched: bool = True):
        self.out, self.src, self.compression, self.chunk_size = out, src, compression, chunk_size
        self.split, self.t0, self.res, self.repeat_latched = split, t0, res, repeat_latched
        self.header: Header | None = None
        self.channels: dict[int, tuple[Channel, Schema | None, str]] = {}   # declared input channels → topic
        self.store: dict[int, deque] = {}
        self.metadata: list[Metadata] = []
        self.attachments: list[Attachment] = []
        self.k = -1                            # index of the open part
        self.part_index = 0                    # duration split: time slot of the open part
        self.fout = self.writer = self.ids = None
        self.part_file: Path | None = None
        self.written = 0

    def declare(self, channel: Channel, schema: Schema | None, latched: bool) -> None:
        """Announce a wanted input channel (registered in every file; latched ones get a message store)."""
        if channel.id in self.channels:
            return
        self.channels[channel.id] = (channel, schema, channel.topic)
        if latched:
            self.store[channel.id] = deque(maxlen=latched_depth(channel))
        if self.ids is not None:
            self.ids.add(channel, schema)

    def remember(self, channel_id: int, rec: Message) -> None:
        """Keep a latched message from before the window (only the last ``depth`` per channel stay)."""
        if channel_id in self.store:
            self.store[channel_id].append(rec)

    def _open(self, start_ns: int) -> None:
        """Start the next file; registers all channels and writes the stored latched messages at ``start_ns``."""
        self.k += 1
        name = part_path(self.out, self.k) if self.split else self.out
        if name.exists() or name.resolve() == self.src.resolve():
            raise FileExistsError(f"{name} exists already; choose a new output name")
        self.part_file = name.with_name(name.name + ".part")
        self.fout = open(self.part_file, "wb")
        self.writer = Writer(self.fout, chunk_size=self.chunk_size, compression=self.compression)
        _start(self.writer, self.header)
        self.ids = _Channels(self.writer)
        for ch, schema, _ in self.channels.values():
            self.ids.add(ch, schema)
        for cid, msgs in (self.store.items() if self.k == 0 or self.repeat_latched else ()):
            topic = self.channels[cid][2]
            for m in msgs:
                self.writer.add_message(self.ids.channel_ids[cid], log_time=start_ns, data=m.data,
                                        publish_time=m.publish_time, sequence=m.sequence)
                self.res.latched[topic] += 1
                self.written += 1
        for m in self.metadata:
            self.writer.add_metadata(m.name, dict(m.metadata))
        if self.k == 0:
            for a in self.attachments:
                self.writer.add_attachment(a.create_time, a.log_time, a.name, a.media_type, a.data)

    def _close(self) -> None:
        """Finish the open file and give it its final name."""
        self.writer.finish()
        self.fout.close()
        final = self.part_file.with_name(self.part_file.name.removesuffix(".part"))
        self.part_file.rename(final)
        self.res.outputs.append(final)
        self.fout = self.writer = self.ids = self.part_file = None

    def add(self, channel_id: int, rec: Message) -> None:
        """Write a message of the window, starting a new file first if the split says so."""
        if self.writer is None:
            self._open(self.t0)
        elif self.split and self.split[0] == "duration":
            slot = int((rec.log_time - self.t0) // self.split[1])
            if slot > self.part_index:
                self._close()
                self.part_index = slot
                self._open(self.t0 + round(slot * self.split[1]))
        elif self.split and self.fout.tell() >= self.split[1]:
            self._close()
            self._open(rec.log_time)
        self.writer.add_message(self.ids.channel_ids[channel_id], log_time=rec.log_time, data=rec.data,
                                publish_time=rec.publish_time, sequence=rec.sequence)
        self.res.kept[self.channels[channel_id][2]] += 1
        self.written += 1
        if self.split and self.repeat_latched:  # only later parts need window messages again
            self.remember(channel_id, rec)

    def add_metadata(self, m: Metadata) -> None:
        """Metadata goes into every file (also files opened later)."""
        self.metadata.append(m)
        if self.writer is not None:
            self.writer.add_metadata(m.name, dict(m.metadata))

    def add_attachment(self, a: Attachment) -> None:
        """Attachments go into one file only (they can be large): the first, or the open one if it comes later."""
        if self.writer is not None:
            self.writer.add_attachment(a.create_time, a.log_time, a.name, a.media_type, a.data)
        else:
            self.attachments.append(a)

    def finish(self) -> None:
        """Close the last file; a window without any message still gives one (empty) file."""
        if self.writer is None:
            self._open(self.t0)
        self._close()

    def abort(self) -> None:
        """Remove everything this run wrote (open .part file and finished parts)."""
        if self.fout is not None:
            self.fout.close()
        if self.part_file is not None:
            self.part_file.unlink(missing_ok=True)
        for p in self.res.outputs:
            p.unlink(missing_ok=True)
        self.res.outputs.clear()


def _start(writer: Writer, header: Header) -> None:
    """Write the output header, keeping the input's profile (e.g. "ros2") and noting this tool as library."""
    writer.start(profile=header.profile, library=f"{header.library} + {LIBRARY}".strip(" +"))


def _copy_indexed(fin, output: _Output, topics: set[str], latched: set[str], t0: int, t1: int, progress, size: int) -> None:
    """Copy using the chunk index: read only the chunks that can contain wanted messages, one at a time."""
    reader = make_reader(fin)
    output.header = reader.get_header()
    summary = reader.get_summary()
    wanted = {cid for cid, ch in summary.channels.items() if ch.topic in topics}
    latched_ids = {cid for cid in wanted if summary.channels[cid].topic in latched}
    for cid in sorted(wanted):                 # declare up front so topics without messages in the window stay visible
        ch = summary.channels[cid]
        output.declare(ch, summary.schemas.get(ch.schema_id), cid in latched_ids)
    for m in reader.iter_metadata():
        output.add_metadata(m)
    for a in reader.iter_attachments():
        output.add_attachment(a)

    def has(ci, ids: set[int]) -> bool:
        """Whether chunk ``ci`` may contain one of the channels ``ids`` (unknown without message indexes → yes)."""
        return not ci.message_index_offsets or not ids.isdisjoint(ci.message_index_offsets)

    pre = [ci for ci in summary.chunk_indexes if latched_ids and ci.message_start_time < t0 and has(ci, latched_ids)]
    win = [ci for ci in summary.chunk_indexes
           if ci.message_end_time >= t0 and ci.message_start_time <= t1 and has(ci, wanted)]
    total = sum(ci.chunk_length for ci in pre + win) or 1
    for phase, chunk_list in (("pre", pre), ("win", win)):
        for ci in sorted(chunk_list, key=lambda c: c.chunk_start_offset):
            fin.seek(ci.chunk_start_offset + 1 + 8, io.SEEK_SET)   # skip opcode (1 byte) and record length (8 bytes)
            chunk = Chunk.read(ReadDataStream(fin))
            for rec in breakup_chunk(chunk):
                if not isinstance(rec, Message) or rec.channel_id not in wanted:
                    continue
                if phase == "pre":
                    if rec.log_time < t0:
                        output.remember(rec.channel_id, rec)
                elif t0 <= rec.log_time <= t1:
                    output.add(rec.channel_id, rec)
            del chunk
            output.res.read_bytes += ci.chunk_length
            progress(output.res.read_bytes / total, output.written)


def _copy_stream(fin, output: _Output, topics: set[str], latched: set[str], t0: int, t1: int, progress, size: int) -> None:
    """Copy by reading every record once from front to back (files without chunk index)."""
    schemas: dict[int, Schema] = {}
    seen: set[int] = set()
    wanted: set[int] = set()
    for rec in StreamReader(fin).records:
        if isinstance(rec, Header):
            output.header = rec
        elif isinstance(rec, Schema):
            schemas.setdefault(rec.id, rec)
        elif isinstance(rec, Channel):
            if rec.id in seen:
                continue                       # channels are repeated in the summary section
            seen.add(rec.id)
            if rec.topic in topics:
                wanted.add(rec.id)
                output.declare(rec, schemas.get(rec.schema_id), rec.topic in latched)
        elif isinstance(rec, Message):
            if rec.channel_id not in wanted:
                continue
            if t0 <= rec.log_time <= t1:
                output.add(rec.channel_id, rec)
            elif rec.log_time < t0:
                output.remember(rec.channel_id, rec)
        elif isinstance(rec, Metadata):
            output.add_metadata(rec)
        elif isinstance(rec, Attachment):
            output.add_attachment(rec)
        progress(fin.tell() / size, output.written)
    if output.header is None:
        raise ValueError("no MCAP header found")
    output.res.read_bytes = size
