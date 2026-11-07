"""MCAP Editor UI: cut, filter and split MCAP recordings in the browser, in the terminal or with plain commands.

  mcap-editor                                  browser UI, file browser in the current folder
  mcap-editor <in.mcap | folder>               browser UI with this recording / in this folder
  mcap-editor ui [path] [--port P] [--no-browser]   (also: mcap-editor-ui [path] ...)
  mcap-editor tui <in.mcap>                    interactive in the terminal
  mcap-editor info <in.mcap>                   topics, message counts, rates, sizes, duration
  mcap-editor cut <in.mcap> [-o out] [--keep T ... | --drop T ...] [--start S] [--end S] ...
  mcap-editor init <in.mcap> [-o job.yaml]     write a job file with all topics, to edit by hand
  mcap-editor run <job.yaml>                   execute a job file

Times are in s after the first message of the recording; an end beyond the recording means "until the end".
'mcap-editor <command> -h' shows the options of a command.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..core import settings
from ..core.bag import BagInfo, read_info
from ..core.edit import Progress, edit, window_ns
from ..core.sizes import TopicSize, fmt_bytes, fmt_share, read_sizes, table_unit
from ..core.job import Job, default_output, did_you_mean, dump_job, job_for, load_job

COMMANDS = ("info", "cut", "init", "run", "tui", "ui")


def file_header(info: BagInfo, sizes: dict[str, TopicSize] | None = None) -> list[str]:
    """Lines above the topic table: file, size, compression, start, duration, and the size disclaimer."""
    t0 = datetime.fromtimestamp(info.start_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"{info.path}  ({fmt_bytes(info.size)}, {info.compression or 'uncompressed'}"
             f"{'' if info.indexed else ', NO INDEX: whole file is read'})",
             f"start {t0}, duration {info.duration_s:.3f} s, {len(info.topics)} topics"]
    if sizes:
        lines.append("size: estimated compressed share of the file, from the message indexes")
    return lines


def topic_table(info: BagInfo, sizes: dict[str, TopicSize] | None = None) -> tuple[str, dict[str, str]]:
    """Column header and one row per topic (sorted): name, messages, rate, size, share, schema, latched."""
    w = max((len(t) for t in info.topics), default=5)
    unit = table_unit(info.size)
    size_hdr = f"  {'size':>12}  {'share':>7}" if sizes else ""
    header = f"{'topic':<{w}}  {'msgs':>9}  {'rate/Hz':>8}{size_hdr}  schema"
    total = sum(s.stored for s in sizes.values()) if sizes else 0
    rows = {}
    for t, ti in sorted(info.topics.items()):
        rate = f"{ti.count / info.duration_s:8.2f}" if ti.count and info.duration_s > 0 else f"{'':>8}"
        count = f"{ti.count:9d}" if ti.count is not None else f"{'?':>9}"
        size = f"  {fmt_bytes(sizes[t].stored, unit):>12}  {fmt_share(sizes[t].stored, total):>7}" if sizes else ""
        rows[t] = f"{t:<{w}}  {count}  {rate}{size}  {ti.schema}{'  [latched]' if ti.latched else ''}"
    return header, rows


def print_info(info: BagInfo, selected: set[str] | None = None, sizes: dict[str, TopicSize] | None = None) -> None:
    """Print the overview table of a recording; with ``selected``, mark kept (+) and dropped (-) topics.

    ``sizes`` (from ``read_sizes``) adds the estimated share of the file per topic.
    """
    print("\n".join(file_header(info, sizes)))
    header, rows = topic_table(info, sizes)
    print(f"  {'':2}{header}")
    for t, row in rows.items():
        mark = "" if selected is None else ("+ " if t in selected else "- ")
        print(f"  {mark:2}{row}")


def run_job(job: Job, info: BagInfo | None = None) -> int:
    """Execute ``job``, printing the plan, progress and a summary; returns the exit code."""
    info = info or read_info(job.input)
    try:
        topics = job.selected(info)
        t0, t1 = window_ns(info, job.start, job.end)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not topics:
        print("ERROR: no topic selected – nothing written", file=sys.stderr)
        return 1
    out = Path(job.output) if job.output else default_output(info.path)
    dropped = sorted(set(info.topics) - topics)
    print(f"input:  {info.path} ({fmt_bytes(info.size)})")
    print(f"output: {out}")
    print(f"window: {(t0 - info.start_ns) / 1e9:.3f} s … {(t1 - info.start_ns) / 1e9:.3f} s"
          f" of {info.duration_s:.3f} s")
    print(f"topics: {len(topics)} kept, {len(dropped)} dropped" + (f" ({', '.join(dropped)})" if dropped else ""))
    progress = Progress()
    try:
        res = edit(info.path, out, topics, job.start, job.end, job.keep_latched, job.extra_latched, job.latched_off,
                   compression=job.compression, split=job.split_spec(), split_repeat_latched=job.split_repeat_latched,
                   progress=progress, info=info)
    except (FileExistsError, ValueError) as e:
        progress.finish()
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        progress.finish()
    w = max(len(t) for t in res.kept)
    for t, n in res.kept.items():
        extra = f"  + {res.latched[t]} latched from before the start" if res.latched.get(t) else ""
        print(f"  {t:<{w}}  {n:9d}{extra}")
    if len(res.outputs) > 1:
        for p in res.outputs:
            print(f"  part {p}  ({fmt_bytes(p.stat().st_size)})")
    print(f"written: {res.outputs[0] if len(res.outputs) == 1 else f'{len(res.outputs)} parts'} ({fmt_bytes(res.out_size)}) in {res.seconds:.1f} s,"
          f" read {fmt_bytes(res.read_bytes)} of {fmt_bytes(info.size)}")
    return 0


def _cmd_info(a) -> int:
    info = read_info(a.src)
    print_info(info, sizes=read_sizes(a.src, info))
    return 0


def _cmd_cut(a) -> int:
    info = read_info(a.src)
    if a.keep and a.drop:
        print("ERROR: use either --keep or --drop", file=sys.stderr)
        return 1
    listed = a.keep or a.drop or []
    missing = [t for t in listed if t not in info.topics]
    if missing:
        print(f"ERROR: not in the recording: {', '.join(missing)}{did_you_mean(missing, info.topics)}", file=sys.stderr)
        return 1
    if a.split_duration and a.split_size:
        print("ERROR: use either --split-duration or --split-size", file=sys.stderr)
        return 1
    st = settings.load()
    split = "duration" if a.split_duration else "size" if a.split_size else st["output.split"]
    job = Job(input=a.src, output=a.out, start=a.start, end=a.end,
              keep_latched=st["output.keep_latched"] and not a.no_latched,
              extra_latched=a.latched or [], latched_off=a.latched_off or [], topics=dict.fromkeys(listed, bool(a.keep)),
              other_topics="drop" if a.keep else "keep", compression=a.compression or st["output.compression"],
              split=split, split_duration_s=a.split_duration or st["output.split_duration_s"],
              split_size_gb=a.split_size or st["output.split_size_gb"],
              split_repeat_latched=st["output.split_repeat_latched"] and not a.latched_first_part_only)
    if job.output is None:
        job.output = default_output(a.src)
    if a.save_job:
        dump_job(job, a.save_job, info)
        print(f"job saved: {a.save_job}")
    return run_job(job, info)


def _cmd_init(a) -> int:
    info = read_info(a.src)
    path = a.out or a.src.with_name(f"{a.src.stem}.job.yaml")
    if path.exists():
        print(f"ERROR: {path} exists already", file=sys.stderr)
        return 1
    dump_job(job_for(info), path, info)
    print(f"job written: {path}\nedit it, then: mcap-editor run {path}")
    return 0


def _cmd_run(a) -> int:
    try:
        job = load_job(a.job)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return run_job(job)


def _cmd_tui(a) -> int:
    from ..tui.wizard import wizard
    return wizard(a.src)


def _cmd_ui(a) -> int:
    from ..ui.server import serve
    path = (a.path or Path.cwd()).expanduser().resolve()
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1
    return serve(path, a.port or settings.get("ui.port"), open_browser=not a.no_browser)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with one subcommand per action."""
    p = argparse.ArgumentParser(prog="mcap-editor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="show topics, counts, rates and duration")
    s.add_argument("src", type=Path)
    s.set_defaults(func=_cmd_info)

    s = sub.add_parser("cut", help="write a copy with selected topics and/or time window")
    s.add_argument("src", type=Path, help="input MCAP (not modified)")
    s.add_argument("-o", "--out", type=Path, help="output MCAP (must not exist; default <in>_edit.mcap)")
    s.add_argument("--keep", nargs="+", metavar="TOPIC", help="keep only these topics")
    s.add_argument("--drop", nargs="+", metavar="TOPIC", help="drop these topics, keep the rest")
    s.add_argument("--start", type=float, default=0.0, help="window start in s after the first message (default 0)")
    s.add_argument("--end", type=float, help="window end in s after the first message (default: until the end)")
    s.add_argument("--no-latched", action="store_true", help="do not include latched messages from before the start")
    s.add_argument("--latched", nargs="+", metavar="TOPIC", help="treat these topics as latched as well")
    s.add_argument("--latched-off", nargs="+", metavar="TOPIC", help="do not include the earlier latched messages of these topics")
    s.add_argument("--compression", choices=["keep", "zstd", "lz4", "none"],
                   help="chunk compression of the output (default: setting output.compression, normally keep = as input)")
    s.add_argument("--split-duration", type=float, metavar="S", help="split into parts of S seconds (<out>_0.mcap, _1 …)")
    s.add_argument("--split-size", type=float, metavar="GB", help="split into parts of about GB gigabytes")
    s.add_argument("--latched-first-part-only", action="store_true",
                   help="when splitting, write latched messages only into part 0 (default: every part)")
    s.add_argument("--save-job", type=Path, metavar="YAML", help="also save the settings as a job file")
    s.set_defaults(func=_cmd_cut)

    s = sub.add_parser("init", help="write a job file listing all topics, to edit and then 'run'")
    s.add_argument("src", type=Path)
    s.add_argument("-o", "--out", type=Path, help="job file (default <in>.job.yaml)")
    s.set_defaults(func=_cmd_init)

    s = sub.add_parser("run", help="execute a job file")
    s.add_argument("job", type=Path)
    s.set_defaults(func=_cmd_run)

    s = sub.add_parser("ui", help="browser UI (default; only reachable from this computer)",
                       description=UI_HELP)
    add_ui_args(s)

    s = sub.add_parser("tui", help="interactive in the terminal")
    s.add_argument("src", type=Path)
    s.set_defaults(func=_cmd_tui)
    return p


UI_HELP = ("Start the browser UI and open it in the default browser. The server listens only on 127.0.0.1, so nobody "
           "else on the network can reach it; Ctrl-C stops it.")


def add_ui_args(s: argparse.ArgumentParser) -> None:
    """Options of the browser UI (shared by 'mcap-editor ui' and 'mcap-editor-ui')."""
    s.add_argument("path", type=Path, nargs="?", help="folder to start in, or a recording to open (default: current folder)")
    s.add_argument("--port", type=int, help="port on 127.0.0.1 (next free one if taken; default: setting ui.port, 8780)")
    s.add_argument("--no-browser", action="store_true", help="only start the server, do not open a browser")
    s.set_defaults(func=_cmd_ui)


def ui_main(argv: list[str] | None = None) -> int:
    """Entry point of ``mcap-editor-ui``: the browser UI directly (same options as ``mcap-editor ui``)."""
    p = argparse.ArgumentParser(prog="mcap-editor-ui", description=UI_HELP)
    add_ui_args(p)
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return a.func(a)
    except KeyboardInterrupt:
        return 130


def main(argv: list[str] | None = None) -> int:
    """Entry point of ``mcap-editor``; without a command (or with only a path / UI options) it starts the browser UI."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["ui", *argv]
    a = build_parser().parse_args(argv)
    for name in ("src", "job"):
        path = getattr(a, name, None)
        if path is not None and not path.is_file():
            print(f"ERROR: {path} not found", file=sys.stderr)
            return 1
    try:
        return a.func(a)
    except KeyboardInterrupt:
        print("\naborted – input unchanged, no output written", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
