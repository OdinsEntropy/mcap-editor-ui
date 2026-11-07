"""Interactive wizard: pick topics with the arrow keys and space, type the time window, confirm, write.

The choices are also saved as a job file next to the output (``<out>.job.yaml``), so the same edit can be
repeated with ``mcap-editor run``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import questionary

from ..core import settings
from ..core.bag import BagInfo, read_info
from ..cli.main import file_header, print_info, run_job, topic_table
from ..core.edit import window_ns
from ..core.job import Job, default_output, dump_job
from ..core.sizes import TopicSize, estimate_output, fmt_bytes, read_sizes, table_unit


def _float_or_empty(lo: float, hi: float | None = None, allow_empty: bool = False):
    """questionary validator: a number in [lo, hi) (or empty if allowed); returns True or an error text."""
    def check(text: str):
        text = text.strip()
        if not text:
            return True if allow_empty else "enter a number"
        try:
            v = float(text)
        except ValueError:
            return "enter a number in s"
        if v < lo:
            return f"must be ≥ {lo:g}"
        if hi is not None and v >= hi:
            return f"must be < {hi:g}"
        return True
    return check


def _ask(question):
    """Ask and abort cleanly on Ctrl-C (questionary returns None then)."""
    answer = question.ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def choose(info: BagInfo, sizes: dict[str, TopicSize] | None = None) -> Job:
    """Ask the user for topics, window, latched handling and output; returns the job."""
    header, rows = topic_table(info, sizes)
    # column header as a non-selectable line; 2 spaces align it with the "○ " in front of each topic
    choices = [questionary.Separator("  " + header)]
    # all unticked: tick what to keep, or press i to invert and untick what to drop
    choices += [questionary.Choice(row, value=t, checked=False) for t, row in rows.items()]
    topics = _ask(questionary.checkbox("Topics to keep", choices=choices,
                                       instruction="(space = toggle, i = invert, a = all, enter = done)",
                                       validate=lambda s: bool(s) or "select at least one topic"))
    dur = info.duration_s
    start = float(_ask(questionary.text(f"Start in s after the first message (recording is {dur:.3f} s)",
                                        default="0", validate=_float_or_empty(0, max(dur, 1e-9)))))
    end_txt = _ask(questionary.text("End in s after the first message (empty = until the end)",
                                    validate=_float_or_empty(start + 1e-9, allow_empty=True))).strip()
    end = float(end_txt) if end_txt else None
    if end is not None and end >= dur:
        end = None
    keep_latched = settings.get("output.keep_latched")
    latched = [t for t in topics if info.topics[t].latched]
    if latched and start > 0:
        keep_latched = _ask(questionary.confirm(f"Include latched messages from before the start ({', '.join(latched)})?",
                                                default=keep_latched, auto_enter=False))
    default_out = default_output(info.path)
    # plain text prompt: questionary.path reserves empty lines for a completion menu
    out = Path(_ask(questionary.text("Output file", default=str(default_out),
                                     validate=lambda p: (bool(p.strip()) and not Path(p).expanduser().exists())
                                     or "file exists – choose a new name"))).expanduser()
    st = settings.load()                 # compression and split: from the settings (change them in the browser UI or the file)
    return Job(input=info.path, output=out, start=start, end=end, keep_latched=keep_latched,
               topics={t: t in topics for t in info.topics}, other_topics="drop", compression=st["output.compression"],
               split=st["output.split"], split_duration_s=st["output.split_duration_s"], split_size_gb=st["output.split_size_gb"],
               split_repeat_latched=st["output.split_repeat_latched"])


def _discard_typeahead() -> None:
    """Drop keys typed while writing (e.g. an extra Enter), so they do not reach the shell after exit."""
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (ImportError, OSError, ValueError):
        pass


def wizard(src: Path) -> int:
    """Run the wizard for ``src``; returns the exit code."""
    if not sys.stdin.isatty():
        print("ERROR: the wizard needs an interactive terminal; use 'mcap-editor cut' or 'mcap-editor run'", file=sys.stderr)
        return 1
    info = read_info(src)
    if not info.topics:
        print(f"ERROR: {src} contains no topics", file=sys.stderr)
        return 1
    sizes = read_sizes(info.path, info)
    print("\n".join(file_header(info, sizes)) + "\n")
    job = choose(info, sizes)
    print()
    selected = {t for t, keep in job.topics.items() if keep}
    print_info(info, selected=selected, sizes=sizes)
    end = "the end" if job.end is None else f"{job.end:g} s"
    est = f", estimated size ≈ {fmt_bytes(estimate_output(sizes, selected, *window_ns(info, job.start, job.end)), table_unit(info.size))}" if sizes else ""
    print(f"\nwindow {job.start:g} s … {end}, output {job.output}{est}")
    if not _ask(questionary.confirm("Write?", default=True, auto_enter=False)):
        print("nothing written")
        return 0
    job_path = Path(job.output).with_suffix(".job.yaml")
    try:
        code = run_job(job, info)
    finally:
        _discard_typeahead()
    if code == 0 and not job_path.exists():
        dump_job(job, job_path, info)
        print(f"job saved: {job_path}  (repeat with: mcap-editor run {job_path})")
    return code
