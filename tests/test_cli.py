"""Tests of the command line and of job files."""

from __future__ import annotations

import re

from mcap.reader import make_reader

from mcap_editor_ui.core.bag import read_info
from mcap_editor_ui.cli.main import main
from mcap_editor_ui.core.job import default_output, dump_job, job_for, load_job


def topics_of(path) -> set[str]:
    """Topics with at least one message in ``path``."""
    with open(path, "rb") as f:
        return {ch.topic for _, ch, _ in make_reader(f).iter_messages(log_time_order=False)}


def test_cut_drop(bag, tmp_path, capsys):
    out = tmp_path / "o.mcap"
    assert main(["cut", str(bag), "-o", str(out), "--drop", "/a", "/c", "--start", "2", "--end", "3"]) == 0
    assert topics_of(out) == {"/b", "/tf_static", "/robot_description", "/ros1_latched"}
    assert "latched from before the start" in capsys.readouterr().out


def test_cut_keep_default_output(bag):
    assert main(["cut", str(bag), "--keep", "/b"]) == 0
    assert topics_of(bag.with_name("rec_edit.mcap")) == {"/b"}
    assert default_output(bag).name == "rec_edit2.mcap"   # next default does not collide


def test_cut_unknown_topic_suggests(bag, capsys):
    assert main(["cut", str(bag), "--drop", "tf_static"]) == 1
    assert "did you mean: /tf_static?" in capsys.readouterr().err


def test_info(bag, capsys):
    assert main(["info", str(bag)]) == 0
    out = capsys.readouterr().out
    assert "/tf_static" in out and "[latched]" in out and "duration 10.000 s" in out


def test_job_roundtrip_and_run(bag, tmp_path):
    info = read_info(bag)
    job = job_for(info, tmp_path / "out.mcap")
    job.topics["/a"] = False
    job.start, job.end = 1.0, 2.0
    path = tmp_path / "job.yaml"
    dump_job(job, path, info)
    loaded = load_job(path)
    assert (loaded.input, loaded.output, loaded.start, loaded.end, loaded.topics) == \
        (bag.resolve(), (tmp_path / "out.mcap").resolve(), 1.0, 2.0, job.topics)
    assert main(["run", str(path)]) == 0
    assert "/a" not in topics_of(tmp_path / "out.mcap")
    assert main(["run", str(path)]) == 1                  # output exists now: refused, not overwritten


def test_init_writes_editable_job(bag, tmp_path):
    assert main(["init", str(bag), "-o", str(tmp_path / "j.yaml")]) == 0
    text = (tmp_path / "j.yaml").read_text()
    assert re.search(r"^  /tf_static: +true +# 1 msgs, .*latched$", text, re.M)
    assert load_job(tmp_path / "j.yaml").topics["/a"] is True


def test_wizard_needs_tty(bag, capsys):
    assert main(["tui", str(bag)]) == 1               # pytest's stdin is not a terminal
    assert "interactive terminal" in capsys.readouterr().err


def test_fmt_share():
    from mcap_editor_ui.cli.main import fmt_share
    assert (fmt_share(1, 3), fmt_share(1, 10**6), fmt_share(0, 5)) == ("33.33%", "~0.00%", "0.00%")


def test_cut_split_and_compression(bag, tmp_path):
    out = tmp_path / "o.mcap"
    assert main(["cut", str(bag), "-o", str(out), "--keep", "/a", "--split-duration", "4", "--compression", "lz4"]) == 0
    parts = sorted(tmp_path.glob("o_*.mcap"))
    assert [p.name for p in parts] == ["o_0.mcap", "o_1.mcap", "o_2.mcap"] and not out.exists()
    with open(parts[0], "rb") as f:
        assert {c.compression for c in make_reader(f).get_summary().chunk_indexes} == {"lz4"}


def test_settings_change_defaults(bag, tmp_path):
    from mcap_editor_ui.core import settings
    settings.save({"output.suffix": "_cut", "output.compression": "none"})
    assert settings.overrides() == {"output.suffix": "_cut", "output.compression": "none"}
    assert main(["cut", str(bag), "--keep", "/b"]) == 0
    out = bag.with_name("rec_cut.mcap")
    with open(out, "rb") as f:
        assert {c.compression for c in make_reader(f).get_summary().chunk_indexes} == {""}
    settings.save(settings.defaults())                    # all defaults again → the override file is removed
    assert not settings.user_file().exists()


def test_default_is_gui(bag, monkeypatch):
    """No command, a path or only GUI options start the GUI (the server itself is replaced here)."""
    import mcap_editor_ui.ui.server as srv
    calls = []
    monkeypatch.setattr(srv, "serve", lambda path, port, open_browser=True: calls.append((path, port, open_browser)) or 0)
    assert main([]) == 0 and main([str(bag)]) == 0 and main(["--no-browser", "--port", "9100"]) == 0
    assert calls[1][0] == bag.resolve() and calls[2][1:] == (9100, False)


def test_ui_command_and_entry_point(bag, monkeypatch):
    """'mcap-editor ui …' and the separate 'mcap-editor-ui' command start the same browser UI."""
    import mcap_editor_ui.ui.server as srv
    from mcap_editor_ui.cli.main import ui_main
    calls = []
    monkeypatch.setattr(srv, "serve", lambda path, port, open_browser=True: calls.append((path, port, open_browser)) or 0)
    assert main(["ui", str(bag), "--port", "9200"]) == 0 and ui_main([str(bag), "--no-browser"]) == 0
    assert calls[0] == (bag.resolve(), 9200, True) and calls[1] == (bag.resolve(), 8780, False)
