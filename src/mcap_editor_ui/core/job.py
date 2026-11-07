"""Edit jobs: everything one edit needs (input, output, topics, time window), saved as a small YAML file.

A job file makes an edit repeatable: ``mcap-editor init`` writes one with all topics of a recording, the user
switches topics off or sets the window, and ``mcap-editor run`` executes it. The wizard saves its choices the same way.
Relative paths in a job file are relative to the job file.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .bag import BagInfo


@dataclass
class Job:
    """One edit. ``topics`` maps topic → keep; topics of the recording that are not listed follow ``other_topics``."""

    input: Path
    output: Path | None = None
    start: float = 0.0                 # s after the first message
    end: float | None = None           # s after the first message; None = until the end
    keep_latched: bool = True          # copy latched messages from before the start (if their topic is kept)
    extra_latched: list[str] = field(default_factory=list)
    latched_off: list[str] = field(default_factory=list)   # latched topics whose earlier messages are not included
    topics: dict[str, bool] = field(default_factory=dict)
    other_topics: str = "keep"         # "keep" or "drop"
    compression: str = "keep"          # keep (as input), zstd, lz4, none
    split: str = "off"                 # off, duration, size
    split_duration_s: float = 60.0
    split_size_gb: float = 1.0
    split_repeat_latched: bool = True  # latched messages at the start of every part (False: only part 0)

    def split_spec(self):
        """Split setting for ``edit()`` (None, ("duration", ns) or ("size", bytes))."""
        from .edit import parse_split
        return parse_split(self.split, self.split_duration_s, self.split_size_gb)

    def selected(self, info: BagInfo) -> set[str]:
        """Topics of ``info`` to write. Raises ValueError for listed topics that are not in the recording."""
        if self.other_topics not in ("keep", "drop"):
            raise ValueError(f"other_topics must be 'keep' or 'drop', not {self.other_topics!r}")
        missing = [t for t in self.topics if t not in info.topics]
        if missing:
            raise ValueError(f"not in {info.path.name}: {', '.join(missing)}{did_you_mean(missing, info.topics)}")
        default = self.other_topics == "keep"
        return {t for t in info.topics if self.topics.get(t, default)}


def did_you_mean(missing: list[str], present) -> str:
    """Hint with similar topic names (ignoring '/' and '_'), or an empty string."""
    def norm(s: str) -> str:
        return s.strip("/").replace("_", "").replace("/", "").lower()
    def close(m: str, p: str) -> bool:
        return bool(norm(m)) and (norm(m) in norm(p) or difflib.SequenceMatcher(None, norm(m), norm(p)).ratio() > 0.8)
    similar = sorted(p for p in present if any(close(m, p) for m in missing))
    return f" – did you mean: {', '.join(similar)}?" if similar else ""


def default_output(src: Path, suffix: str | None = None) -> Path:
    """``<stem><suffix>.mcap`` next to the input, numbered (``_edit2`` …) if that or its split part 0 exists.

    ``suffix`` defaults to the setting output.suffix ("_edit").
    """
    if suffix is None:
        from . import settings
        suffix = settings.get("output.suffix")
    src = Path(src)
    out, n = src.with_name(f"{src.stem}{suffix}.mcap"), 2
    while any(p.exists() for p in (out, out.with_name(out.name + ".part"), out.with_name(f"{out.stem}_0.mcap"))):
        out, n = src.with_name(f"{src.stem}{suffix}{n}.mcap"), n + 1
    return out


def load_job(path: Path) -> Job:
    """Read a job file; relative paths are taken relative to the job file."""
    path = Path(path)
    d = yaml.safe_load(path.read_text()) or {}
    if "input" not in d:
        raise ValueError(f"{path}: 'input' missing")
    base = path.parent

    def p(v):
        return None if v in (None, "") else (base / Path(v).expanduser())

    topics = d.get("topics") or {}
    if not isinstance(topics, dict):
        raise ValueError(f"{path}: 'topics' must be a mapping 'topic: true/false'")
    return Job(input=p(d["input"]), output=p(d.get("output")), start=float(d.get("start") or 0.0),
               end=None if d.get("end") is None else float(d["end"]), keep_latched=bool(d.get("keep_latched", True)),
               extra_latched=list(d.get("extra_latched") or []), latched_off=list(d.get("latched_off") or []), topics={str(k): bool(v) for k, v in topics.items()},
               other_topics=str(d.get("other_topics", "keep")), compression=str(d.get("compression", "keep")),
               split=str(d.get("split", "off")), split_duration_s=float(d.get("split_duration_s", 60.0)),
               split_size_gb=float(d.get("split_size_gb", 1.0)),
               split_repeat_latched=bool(d.get("split_repeat_latched", True)))


def job_for(info: BagInfo, output: Path | None = None) -> Job:
    """A job for ``info`` that keeps everything, with the output options from the settings (starting point to edit)."""
    from . import settings
    st = settings.load()
    return Job(input=info.path, output=output or default_output(info.path), topics=dict.fromkeys(info.topics, True),
               keep_latched=st["output.keep_latched"], compression=st["output.compression"], split=st["output.split"],
               split_duration_s=st["output.split_duration_s"], split_size_gb=st["output.split_size_gb"],
               split_repeat_latched=st["output.split_repeat_latched"])


def dump_job(job: Job, path: Path, info: BagInfo | None = None) -> None:
    """Write ``job`` as a commented YAML file (with message counts per topic if ``info`` is given)."""
    def q(v) -> str:
        return yaml.safe_dump(v, default_flow_style=True, width=10**6).strip().removesuffix("\n...").strip()

    lines = [
        "# mcap-editor job – run with: mcap-editor run <this file>",
        f"input: {q(str(Path(job.input).resolve()))}",
        f"output: {q(str(Path(job.output).resolve())) if job.output else 'null'}",
        f"start: {job.start}        # s after the first message",
        f"end: {'null' if job.end is None else job.end}        # s after the first message; null = until the end"
        + (f" ({info.duration_s:.3f} s)" if info else ""),
        f"keep_latched: {q(job.keep_latched)}   # include latched messages (e.g. /tf_static) from before the start",
        f"extra_latched: {q(job.extra_latched)}   # topics to treat as latched although the file does not say so",
        f"latched_off: {q(job.latched_off)}   # latched topics without their earlier messages",
        f"compression: {job.compression}   # keep (as input), zstd, lz4, none",
        f"split: {q(job.split)}   # off, duration, size – parts are named <output>_0.mcap, _1 …",
        f"split_duration_s: {job.split_duration_s}",
        f"split_size_gb: {job.split_size_gb}",
        f"split_repeat_latched: {q(job.split_repeat_latched)}   # latched messages in every part (false: only part 0)",
        f"other_topics: {job.other_topics}   # what to do with topics of the recording that are not listed below",
        "topics:          # true = keep, false = drop",
    ]
    width = max((len(t) for t in job.topics), default=0) + 1
    for t, keep in job.topics.items():
        note = ""
        if info and t in info.topics:
            ti = info.topics[t]
            note = f"  # {ti.count if ti.count is not None else '?'} msgs, {ti.schema}" + (", latched" if ti.latched else "")
        lines.append(f"  {q(t) + ':':<{width + 2}} {q(keep)}{note}")
    Path(path).write_text("\n".join(lines) + "\n")
