"""Tests of the activity histograms and the message time lookups (synthetic recording with known times)."""

from __future__ import annotations

from conftest import T_BASE, expected_times, write_bag
from mcap_editor_ui.core.bag import read_info
from mcap_editor_ui.core.msgtimes import MessageTimes
from mcap_editor_ui.core.sizes import ACTIVITY_BINS, read_sizes


def test_activity_counts_per_slice(tmp_path):
    src = write_bag(tmp_path / "rec.mcap")
    info = read_info(src)
    sizes = read_sizes(src, info)
    for topic in ("/a", "/b", "/tf_static"):
        act = sizes[topic].activity
        assert act.sum() == len(expected_times(topic)) and len(act) == ACTIVITY_BINS
    # /b: 1 Hz over 10 s → 11 messages in 11 different slices, the first in slice 0 and the last in the last one
    nz = sizes["/b"].activity.nonzero()[0]
    assert len(nz) == 11 and nz[0] == 0 and nz[-1] == ACTIVITY_BINS - 1


def test_step_prev_next_nearest(tmp_path):
    src = write_bag(tmp_path / "rec.mcap")
    mt = MessageTimes(src)
    s = 10**9
    assert mt.step("/b", T_BASE + 2 * s, +1) == T_BASE + 3 * s          # strictly after
    assert mt.step("/b", T_BASE + 2 * s, -1) == T_BASE + 1 * s          # strictly before
    assert mt.step("/b", T_BASE + 2 * s, 0) == T_BASE + 2 * s           # exactly on a message
    assert mt.step("/b", T_BASE + 2.4 * s, 0) == T_BASE + 2 * s         # nearest
    assert mt.step("/b", T_BASE + 2.6 * s, 0) == T_BASE + 3 * s
    assert mt.step("/b", T_BASE + 10 * s, +1) is None                   # after the last
    assert mt.step("/b", T_BASE, -1) is None                            # before the first
    # stepping through all /a messages from the start visits every message once
    t, seen = T_BASE - 1, []
    while (t := mt.step("/a", t, +1)) is not None:
        seen.append(t)
    assert seen == expected_times("/a")


def test_step_without_index(tmp_path):
    src = write_bag(tmp_path / "rec.mcap", indexed=False)
    assert MessageTimes(src).step("/a", T_BASE, +1) is None


def test_activity_for_zoomed_range(tmp_path):
    src = write_bag(tmp_path / "rec.mcap")
    mt = MessageTimes(src)
    s = 10**9
    counts = mt.activity("/a", T_BASE + 2 * s, T_BASE + 4 * s, 20)        # 10 Hz, 2–4 s inclusive → 21 messages
    assert sum(counts) == 21 and len(counts) == 20 and all(c >= 1 for c in counts)
    assert mt.activity("/b", T_BASE + int(2.2 * s), T_BASE + int(2.8 * s), 10) == [0] * 10    # 1 Hz: none inside
