"""Local web server for the browser UI: serves the static frontend and a small JSON API around the edit functions.

The browser cannot hand a local file path to a page (a file input uploads the content and hides the path), so
files are chosen with a file browser that lists folders on the server side. The server therefore only listens on
127.0.0.1, rejects requests with a foreign Host header (DNS rebinding) and accepts POSTs only as JSON (a foreign
web page cannot send those without a CORS preflight, which is never answered).

API (all JSON):
    GET  /api/ls?path=…          folders and .mcap files of a folder
    POST /api/open               {path} → starts opening a recording (summary, message indexes) in the background
    GET  /api/open               state of the opening (stage, done, info when finished)
    POST /api/open/cancel        stop opening
    GET  /api/info?path=…        overview, topic table, default output (from the cache, or computed now)
    POST /api/estimate           {path, topics, start, end} → estimated output size
    GET  /api/output?path=…&split=0|1   whether an output path is usable (+ a free alternative name)
    POST /api/export             {input, output, topics, start, end, keep_latched, latched_off, compression, split,
                                  split_duration_s, split_size_gb} → starts the export
    GET  /api/step?path&topic&t&dir   time (ns after the first message) of the previous (-1) / next (1) / nearest (0)
                                      message of a topic, or null
    POST /api/activity           {path, topics, t0, t1, bins} → messages per slice of [t0, t1] (ns after the first
                                  message) per topic, for the zoomed timeline
    GET  /api/settings           schema, defaults, effective values, user overrides
    POST /api/settings           {values} → saves the overrides (only values that differ from the defaults)
    GET  /api/export             state of the running / last export
    POST /api/cancel             cancel the running export
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ..core.bag import BagInfo, read_info
from ..core import settings
from ..core.edit import edit, parse_split, part_path, window_ns
from ..core.job import Job, default_output, dump_job
from ..core.msgtimes import MessageTimes
from ..core.sizes import ACTIVITY_BINS, TopicSize, estimate_output, fmt_bytes, fmt_share, read_sizes, table_unit

STATIC = Path(__file__).parent / "static"


class Cancelled(Exception):
    """Raised from the progress callback to stop a running export (edit() then deletes its .part file)."""


class ApiError(Exception):
    """Error with an HTTP status, shown to the user as a toast."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


class State:
    """Shared server state: cache of opened recordings and the one export that may run at a time."""

    def __init__(self, start_dir: Path):
        self.start_dir = start_dir
        self.lock = threading.Lock()
        self.cache: dict[Path, tuple[float, int, BagInfo, dict[str, TopicSize] | None]] = {}
        self.export: dict = {"state": "idle"}
        self.cancel = threading.Event()
        self.opening: dict = {"state": "idle"}
        self.times: dict[Path, tuple[float, MessageTimes]] = {}
        self.open_cancel = threading.Event()

    def recording(self, path: Path, progress=None) -> tuple[BagInfo, dict[str, TopicSize] | None]:
        """Overview and sizes of ``path``, cached until the file changes; ``progress(stage, done)`` while reading."""
        st = path.stat()
        hit = self.cache.get(path)
        if hit and hit[:2] == (st.st_mtime, st.st_size):
            return hit[2], hit[3]
        report = progress or (lambda stage, done: None)
        report("Summary", 0.0)
        info = read_info(path, progress=lambda d: report("Summary", d))
        report("Message indexes", 0.0)
        sizes = read_sizes(path, info, progress=lambda d: report("Message indexes", d))
        self.cache[path] = (st.st_mtime, st.st_size, info, sizes)
        return info, sizes


def message_times(path: Path, state: State) -> MessageTimes:
    """Message time lookup for ``path`` (summary kept in memory), renewed when the file changes."""
    mtime = path.stat().st_mtime
    hit = state.times.get(path)
    if hit is None or hit[0] != mtime:
        hit = state.times[path] = (mtime, MessageTimes(path))
    return hit[1]


def _path(value: str | None, must_exist: bool = True) -> Path:
    """Absolute path from a request parameter (``~`` expanded)."""
    if not value:
        raise ApiError("no path given")
    p = Path(value).expanduser().resolve()
    if must_exist and not p.exists():
        raise ApiError(f"{p} does not exist", HTTPStatus.NOT_FOUND)
    return p


def list_dir(path: Path) -> dict:
    """Folders and .mcap files of ``path`` (hidden entries skipped), folders first."""
    if path.is_file():
        path = path.parent
    entries = []
    try:
        items = list(path.iterdir())
    except PermissionError as e:
        raise ApiError(f"no permission to read {path}", HTTPStatus.FORBIDDEN) from e
    for p in items:
        if p.name.startswith("."):
            continue
        try:
            if p.is_dir():
                entries.append({"name": p.name, "path": str(p), "dir": True})
            elif p.suffix == ".mcap" and p.is_file():
                st = p.stat()
                entries.append({"name": p.name, "path": str(p), "dir": False, "size": st.st_size,
                                "size_text": fmt_bytes(st.st_size), "mtime": st.st_mtime})
        except OSError:
            continue                   # broken link, vanished file
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return {"path": str(path), "parent": str(path.parent) if path.parent != path else None, "entries": entries}


def info_json(path: Path, state: State) -> dict:
    """Overview of a recording for the frontend, with the same formatted numbers as ``mcap-editor info``."""
    info, sizes = state.recording(path)
    unit = table_unit(info.size)
    total = sum(s.stored for s in sizes.values()) if sizes else 0
    topics = []
    for t, ti in sorted(info.topics.items()):
        stored = sizes[t].stored if sizes else None
        topics.append({
            "topic": t, "count": ti.count, "schema": ti.schema, "latched": ti.latched,
            "rate": ti.count / info.duration_s if ti.count and info.duration_s > 0 else None,
            "size": stored, "size_text": fmt_bytes(stored, unit) if sizes else "?",
            "share_text": fmt_share(stored, total) if sizes else "?",
        })
    start = datetime.fromtimestamp(info.start_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    activity = {t: sizes[t].activity.tolist() for t in info.topics} if sizes else None
    return {"path": str(path), "name": path.name, "size": info.size, "size_text": fmt_bytes(info.size),
            "activity": activity, "activity_bins": ACTIVITY_BINS,
            "compression": info.compression or "uncompressed", "indexed": info.indexed, "start_text": start,
            "duration": info.duration_s, "unit": unit, "sizes_known": sizes is not None, "topics": topics,
            "default_output": str(default_output(path))}


def output_json(path: Path, split: bool = False) -> dict:
    """Whether ``path`` can be written (folder exists, file or its part 0 does not), with a free alternative name."""
    ok, problem = True, ""
    target = part_path(path, 0) if split else path
    if path.suffix != ".mcap":
        ok, problem = False, "Name must end in .mcap"
    elif target.exists() or target.with_name(target.name + ".part").exists():
        ok, problem = False, f"{target.name} exists – files are never overwritten"
    elif not path.parent.is_dir():
        ok, problem = False, f"Folder {path.parent} does not exist"
    alt = str(default_output(path, suffix="")) if path.parent.is_dir() and not ok and path.suffix == ".mcap" else None
    return {"path": str(path), "ok": ok, "problem": problem, "alternative": alt}


def settings_json() -> dict:
    """Everything the settings dialog needs: schema, defaults, effective values, the user's overrides and file."""
    return {"schema": settings.SCHEMA, "defaults": settings.defaults(), "values": settings.load(),
            "overrides": settings.overrides(), "file": str(settings.user_file())}


def start_open(path: Path, state: State) -> dict:
    """Open a recording in a background thread (summary, then message indexes), with progress and cancel.

    A new request replaces a running one: the running one is cancelled first.
    """
    if not path.is_file():
        raise ApiError(f"{path} is not a file", HTTPStatus.NOT_FOUND)
    state.open_cancel.set()
    with state.lock:
        old = state.opening.get("thread")
    if old is not None:
        old.join(timeout=5)
    cancel = threading.Event()
    state.open_cancel = cancel
    t_start = time.time()

    def progress(stage: str, done: float) -> None:
        if cancel.is_set():
            raise Cancelled
        with state.lock:
            state.opening.update(stage=stage, done=done, elapsed=time.time() - t_start)

    def run() -> None:
        try:
            state.recording(path, progress)
            final = {"state": "done", "stage": "Done", "done": 1.0, "info": info_json(path, state)}
        except Cancelled:
            final = {"state": "cancelled", "stage": "Cancelled"}
        except Exception as e:  # noqa: BLE001 – reported to the user, the server keeps running
            final = {"state": "error", "stage": "Failed", "error": str(e)}
        with state.lock:
            state.opening.update(final, elapsed=time.time() - t_start)
            state.opening.pop("thread", None)

    thread = threading.Thread(target=run, daemon=True, name="open")
    with state.lock:
        state.opening = {"state": "running", "stage": "Summary", "done": 0.0, "path": str(path), "name": path.name,
                         "size_text": fmt_bytes(path.stat().st_size), "elapsed": 0.0, "thread": thread}
    thread.start()
    return opening_json(state)


def opening_json(state: State) -> dict:
    """The opening state without internal objects."""
    with state.lock:
        return {k: v for k, v in state.opening.items() if k != "thread"}


def start_export(body: dict, state: State) -> dict:
    """Validate the request and run the export in a background thread; progress goes to ``state.export``."""
    src = _path(body.get("input"))
    out = _path(body.get("output"), must_exist=False)
    split = parse_split(body.get("split"), body.get("split_duration_s"), body.get("split_size_gb"))
    compression = str(body.get("compression") or "keep")
    check = output_json(out, split=split is not None)
    if not check["ok"]:
        raise ApiError(check["problem"], HTTPStatus.CONFLICT)
    topics = set(body.get("topics") or [])
    if not topics:
        raise ApiError("select at least one topic")
    start, end = float(body.get("start") or 0.0), body.get("end")
    end = None if end in (None, "") else float(end)
    keep_latched = bool(body.get("keep_latched", True))
    latched_off = [str(t) for t in body.get("latched_off") or []]
    info, _ = state.recording(src)
    window_ns(info, start, end)                      # raises ValueError for an empty window
    with state.lock:
        if state.export.get("state") == "running":
            raise ApiError("an export is already running", HTTPStatus.CONFLICT)
        state.cancel.clear()
        state.export = {"state": "running", "stage": "Preparing", "done": 0.0, "written": 0, "input": str(src),
                        "output": str(out), "started": time.time(), "elapsed": 0.0, "eta": None}

    def progress(done: float, written: int) -> None:
        if state.cancel.is_set():
            raise Cancelled
        with state.lock:
            e = state.export
            e.update(stage="Copying", done=done, written=written, elapsed=time.time() - e["started"])
            e["eta"] = e["elapsed"] / done * (1 - done) if done > 0 else None

    def run() -> None:
        try:
            repeat = bool(body.get("split_repeat_latched", True))
            res = edit(src, out, topics, start, end, keep_latched, latched_off=latched_off, progress=progress, info=info,
                       compression=compression, split=split, split_repeat_latched=repeat)
            job = Job(input=src, output=out, start=start, end=end, keep_latched=keep_latched, latched_off=latched_off,
                      topics={t: t in topics for t in info.topics}, other_topics="drop", compression=compression,
                      split=str(body.get("split") or "off"),
                      split_duration_s=float(body.get("split_duration_s") or 60.0),
                      split_size_gb=float(body.get("split_size_gb") or 1.0), split_repeat_latched=repeat)
            job_path = out.with_suffix(".job.yaml")
            if not job_path.exists():
                dump_job(job, job_path, info)
            final = {"state": "done", "stage": "Done", "done": 1.0, "kept": res.kept, "latched": res.latched,
                     "out_size_text": fmt_bytes(res.out_size), "read_text": fmt_bytes(res.read_bytes),
                     "seconds": res.seconds, "job": str(job_path),
                     "outputs": [{"path": str(p), "size_text": fmt_bytes(p.stat().st_size)} for p in res.outputs]}
        except Cancelled:
            final = {"state": "cancelled", "stage": "Cancelled"}
        except Exception as e:  # noqa: BLE001 – any failure is reported to the user, the server keeps running
            final = {"state": "error", "stage": "Failed", "error": str(e)}
        with state.lock:
            state.export.update(final, elapsed=time.time() - state.export["started"])

    threading.Thread(target=run, daemon=True, name="export").start()
    return state.export


def make_handler(state: State, port_ref: list[int]):
    """Request handler class bound to ``state``; ``port_ref[0]`` is the port (known only after binding)."""

    class Handler(BaseHTTPRequestHandler):
        """Serves static files and the JSON API."""

        server_version = "mcap_editor_ui"

        def log_message(self, fmt, *args):     # keep the terminal quiet; errors are shown in the page
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"}

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")     # browsers must not mix old and new modules
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _route(self, method: str) -> None:
            if not self._host_ok():
                self._json({"error": "forbidden host"}, 403)
                return
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if method == "POST":
                    if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                        raise ApiError("JSON only", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    self._post(url.path, body)
                elif url.path.startswith("/api/"):
                    self._get_api(url.path, q)
                else:
                    self._static(url.path)
            except ApiError as e:
                self._json({"error": str(e)}, e.status)
            except (ValueError, FileExistsError, OSError) as e:
                self._json({"error": str(e)}, 400)

        def _get_api(self, path: str, q: dict) -> None:
            if path == "/api/ls":
                self._json(list_dir(_path(q.get("path") or str(state.start_dir))))
            elif path == "/api/info":
                self._json(info_json(_path(q.get("path")), state))
            elif path == "/api/output":
                self._json(output_json(_path(q.get("path"), must_exist=False), split=q.get("split") == "1"))
            elif path == "/api/settings":
                self._json(settings_json())
            elif path == "/api/step":
                p = _path(q.get("path"))
                info, _ = state.recording(p)
                t = message_times(p, state).step(q.get("topic", ""), info.start_ns + int(q.get("t", 0)), int(q.get("dir", 0)))
                self._json({"t": None if t is None else t - info.start_ns})
            elif path == "/api/export":
                with state.lock:
                    self._json(dict(state.export))
            elif path == "/api/open":
                self._json(opening_json(state))
            elif path == "/api/start":
                self._json({"start_dir": str(state.start_dir)})
            else:
                raise ApiError("unknown API path", HTTPStatus.NOT_FOUND)

        def _post(self, path: str, body: dict) -> None:
            if path == "/api/estimate":
                info, sizes = state.recording(_path(body.get("path")))
                if sizes is None:
                    self._json({"bytes": None, "text": "?"})
                    return
                end = body.get("end")
                t0, t1 = window_ns(info, float(body.get("start") or 0), None if end in (None, "") else float(end))
                est = estimate_output(sizes, set(body.get("topics") or []) & set(info.topics), t0, t1)
                self._json({"bytes": est, "text": fmt_bytes(est, table_unit(info.size))})
            elif path == "/api/export":
                self._json(start_export(body, state))
            elif path == "/api/activity":
                p = _path(body.get("path"))
                info, _ = state.recording(p)
                mt = message_times(p, state)
                t0, t1 = info.start_ns + int(body.get("t0", 0)), info.start_ns + int(body.get("t1", 0))
                bins = max(1, min(int(body.get("bins", 400)), 4000))
                self._json({t: mt.activity(t, t0, t1, bins) for t in body.get("topics") or []})
            elif path == "/api/open":
                self._json(start_open(_path(body.get("path")), state))
            elif path == "/api/open/cancel":
                state.open_cancel.set()
                self._json({"ok": True})
            elif path == "/api/settings":
                settings.save(body.get("values") or {})
                self._json(settings_json())
            elif path == "/api/cancel":
                state.cancel.set()
                self._json({"ok": True})
            else:
                raise ApiError("unknown API path", HTTPStatus.NOT_FOUND)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            f = (STATIC / rel).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                self._json({"error": "not found"}, 404)
                return
            ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}.get(
                f.suffix, mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return Handler


def make_server(start_dir: Path, port: int = 8780) -> ThreadingHTTPServer:
    """Server on 127.0.0.1:``port`` (the next free port if taken; 0 = any free port).

    The default 8780 avoids 8765, the default of foxglove_bridge, which often runs next to this tool.
    """
    port_ref = [port]
    handler = make_handler(State(Path(start_dir).resolve()), port_ref)
    for p in ([port] + list(range(port + 1, port + 20)) if port else [0]):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), handler)
            break
        except OSError:
            continue
    else:
        raise OSError(f"no free port in {port}–{port + 19}")
    port_ref[0] = server.server_address[1]
    server.daemon_threads = True
    return server


def serve(path: Path, port: int = 8780, open_browser: bool = True) -> int:
    """Run the browser UI server until Ctrl-C; ``path`` is the start folder or a recording to open right away."""
    path = Path(path)
    server = make_server(path.parent if path.is_file() else path, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if path.is_file():
        url += f"?open={quote(str(path))}"
    print(f"MCAP Editor UI: {url}  (only reachable from this computer; Ctrl-C stops it)", flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0
