"""Tests of the GUI server API, against a real server on a free port with synthetic recordings."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

from conftest import TOPICS, write_bag
from mcap_editor_ui.ui.server import make_server


@pytest.fixture
def server(tmp_path):
    """GUI server in a thread, starting in ``tmp_path``; yields its base URL."""
    srv = make_server(tmp_path, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(url, body=None, headers=None):
    """GET (or POST JSON with ``body``); returns (status, parsed JSON)."""
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=h | (headers or {}))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_export(base, timeout=10.0):
    """Poll the export state until it is no longer running."""
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        _, s = call(f"{base}/api/export")
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise TimeoutError


def test_ls_info_estimate(server, tmp_path):
    bag = write_bag(tmp_path / "rec.mcap")
    (tmp_path / "sub").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    _, ls = call(f"{server}/api/ls?path={quote(str(tmp_path))}")
    assert [(e["name"], e["dir"]) for e in ls["entries"]] == [("sub", True), ("rec.mcap", False)]   # no .txt
    _, info = call(f"{server}/api/info?path={quote(str(bag))}")
    assert {t["topic"] for t in info["topics"]} == set(TOPICS)
    assert info["default_output"].endswith("rec_edit.mcap") and info["duration"] == pytest.approx(10.0)
    _, est = call(f"{server}/api/estimate", {"path": str(bag), "topics": ["/a"], "start": 2, "end": 4})
    _, full = call(f"{server}/api/estimate", {"path": str(bag), "topics": ["/a"], "start": 0, "end": None})
    assert 0 < est["bytes"] < full["bytes"]


def test_output_check(server, tmp_path):
    bag = write_bag(tmp_path / "rec.mcap")
    _, r = call(f"{server}/api/output?path={quote(str(bag))}")
    assert not r["ok"] and r["alternative"].endswith("rec2.mcap")
    _, r = call(f"{server}/api/output?path={quote(str(tmp_path / 'new.mcap'))}")
    assert r["ok"]
    _, r = call(f"{server}/api/output?path={quote(str(tmp_path / 'nofolder' / 'x.mcap'))}")
    assert not r["ok"] and "does not exist" in r["problem"]


def test_export_writes_output_and_job(server, tmp_path):
    bag = write_bag(tmp_path / "rec.mcap")
    out = tmp_path / "out.mcap"
    status, _ = call(f"{server}/api/export", {"input": str(bag), "output": str(out), "topics": ["/a", "/tf_static"],
                                               "start": 2, "end": 5, "keep_latched": True})
    assert status == 200
    s = wait_export(server)
    assert s["state"] == "done" and s["kept"] == {"/a": 31, "/tf_static": 0} and s["latched"] == {"/tf_static": 1}
    assert out.exists() and (tmp_path / "out.job.yaml").exists()
    status, err = call(f"{server}/api/export", {"input": str(bag), "output": str(out), "topics": ["/a"]})
    assert status == 409 and "never overwritten" in err["error"]


def test_cancel_removes_part(server, tmp_path, monkeypatch):
    bag = write_bag(tmp_path / "rec.mcap")
    import mcap_editor_ui.ui.server as srv
    orig = srv.edit

    def slow_edit(*a, progress=None, **kw):        # slow down every progress step so the cancel arrives in time
        def p(done, written):
            time.sleep(0.05)
            progress(done, written)
        return orig(*a, progress=p, **kw)
    monkeypatch.setattr(srv, "edit", slow_edit)
    out = tmp_path / "out.mcap"
    call(f"{server}/api/export", {"input": str(bag), "output": str(out), "topics": list(TOPICS)})
    time.sleep(0.2)
    call(f"{server}/api/cancel", {})
    assert wait_export(server)["state"] == "cancelled"
    assert not out.exists() and not list(tmp_path.glob("out.mcap*"))


def test_security_checks(server):
    status, _ = call(f"{server}/api/start", headers={"Host": "evil.example:80"})
    assert status == 403                                    # DNS rebinding: foreign Host header
    req = urllib.request.Request(f"{server}/api/cancel", data=b"x", headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 415                              # simple cross-site form posts are refused
    status, _ = call(f"{server}/../../etc/passwd")
    assert status == 404


def test_static_files_served(server):
    with urllib.request.urlopen(f"{server}/") as r:
        assert r.headers["Cache-Control"] == "no-cache" and b"MCAP Editor UI" in r.read()
    with urllib.request.urlopen(f"{server}/js/main.js") as r:
        assert r.headers["Content-Type"].startswith("text/javascript")


def test_settings_api(server):
    _, s = call(f"{server}/api/settings")
    assert s["values"]["interface.accent"] == "orange" and s["overrides"] == {}
    status, s = call(f"{server}/api/settings", {"values": s["values"] | {"interface.accent": "teal"}})
    assert status == 200 and s["overrides"] == {"interface.accent": "teal"}
    status, err = call(f"{server}/api/settings", {"values": {"interface.accent": "pink"}})
    assert status == 400 and "not one of" in err["error"]


def test_export_split_and_compression(server, tmp_path):
    bag = write_bag(tmp_path / "rec.mcap")
    out = tmp_path / "out.mcap"
    call(f"{server}/api/export", {"input": str(bag), "output": str(out), "topics": ["/a"], "compression": "lz4",
                                  "split": "duration", "split_duration_s": 4})
    s = wait_export(server)
    assert s["state"] == "done" and [o["path"].rsplit("/", 1)[1] for o in s["outputs"]] == ["out_0.mcap", "out_1.mcap", "out_2.mcap"]
    _, r = call(f"{server}/api/output?path={quote(str(out))}&split=1")
    assert not r["ok"] and "out_0.mcap exists" in r["problem"]


def test_open_job_and_cancel(server, tmp_path, monkeypatch):
    bag = write_bag(tmp_path / "rec.mcap")
    status, s = call(f"{server}/api/open", {"path": str(bag)})
    assert status == 200
    t_end = time.monotonic() + 5
    while s["state"] == "running" and time.monotonic() < t_end:
        time.sleep(0.05)
        _, s = call(f"{server}/api/open")
    assert s["state"] == "done" and {t["topic"] for t in s["info"]["topics"]} == set(TOPICS)

    import mcap_editor_ui.ui.server as srv
    def slow_sizes(path, info, progress=None):           # a "huge" file: many slow steps, cancellable
        for i in range(200):
            time.sleep(0.02)
            progress(i / 200)
    monkeypatch.setattr(srv, "read_sizes", slow_sizes)
    other = write_bag(tmp_path / "other.mcap")
    call(f"{server}/api/open", {"path": str(other)})
    time.sleep(0.2)
    _, s = call(f"{server}/api/open")
    assert s["state"] == "running" and s["stage"] == "Message indexes" and 0 < s["done"] < 1
    call(f"{server}/api/open/cancel", {})
    time.sleep(0.2)
    _, s = call(f"{server}/api/open")
    assert s["state"] == "cancelled"


def test_activity_and_step_api(server, tmp_path):
    bag = write_bag(tmp_path / "rec.mcap")
    _, info = call(f"{server}/api/info?path={quote(str(bag))}")
    assert sum(info["activity"]["/b"]) == 11 and len(info["activity"]["/b"]) == info["activity_bins"]
    step = lambda t, d: call(f"{server}/api/step?path={quote(str(bag))}&topic=%2Fb&t={t}&dir={d}")[1]["t"]
    s = 10**9
    assert (step(2 * s, 1), step(2 * s, -1), step(int(2.4 * s), 0)) == (3 * s, 1 * s, 2 * s)   # ns after the first message
    assert step(10 * s, 1) is None
