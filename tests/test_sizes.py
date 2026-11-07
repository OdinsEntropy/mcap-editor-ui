"""Tests of the per-topic sizes read from the message indexes."""

from __future__ import annotations

import pytest
from mcap.writer import CompressionType

from conftest import TOPICS, expected_times, payload, write_bag
from mcap_editor_ui.core.bag import read_info
from mcap_editor_ui.core.edit import window_ns
from mcap_editor_ui.core.sizes import estimate_output, fmt_bytes, read_sizes, table_unit


def true_raw(topic: str) -> int:
    """Exact message data bytes of ``topic`` in the synthetic recording."""
    return sum(len(payload(topic, t)) for t in expected_times(topic))


@pytest.mark.parametrize("compression", [CompressionType.NONE, CompressionType.ZSTD])
def test_raw_sizes_exact(tmp_path, compression):
    src = write_bag(tmp_path / "rec.mcap", compression=compression)
    sizes = read_sizes(src, read_info(src))
    # schema/channel records inside a chunk are counted to the message before them: a few hundred bytes at most
    for t in TOPICS:
        assert 0 <= sizes[t].raw - true_raw(t) <= 300, t
    if compression == CompressionType.NONE:
        assert all(s.stored == s.raw for s in sizes.values())


def test_estimate_of_window(tmp_path):
    src = write_bag(tmp_path / "rec.mcap", compression=CompressionType.NONE)
    info = read_info(src)
    sizes = read_sizes(src, info)
    topics = {"/a", "/c"}
    t0, t1 = window_ns(info, 2.0, 6.0)
    est = estimate_output(sizes, topics, t0, t1)
    exact = sum(len(payload(t, ts)) for t in topics for ts in expected_times(t) if t0 <= ts <= t1)
    assert est == pytest.approx(exact, rel=0.05)              # chunk granularity: messages assumed evenly spread
    assert estimate_output(sizes, set(TOPICS), info.start_ns, info.end_ns) == pytest.approx(
        sum(s.stored for s in sizes.values()))


def test_no_sizes_without_index(tmp_path):
    src = write_bag(tmp_path / "rec.mcap", indexed=False)
    assert read_sizes(src, read_info(src)) is None


def test_table_unit_smart_and_fixed():
    assert [table_unit(n, "smart") for n in (5e6, 0.99e9, 3e9, 99e9, 150e9)] == ["kB", "kB", "MB", "MB", "GB"]
    assert table_unit(150e9, "kB") == "kB" and table_unit(5e6, "auto") == "auto"
    with pytest.raises(ValueError):
        table_unit(1, "TB")


def test_fmt_bytes_two_decimals_and_near_zero():
    assert fmt_bytes(176_300_000, "MB") == "176.30 MB"
    assert fmt_bytes(2, "MB") == "~0.00 MB"                   # not zero, but too small for the unit
    assert fmt_bytes(0, "MB") == "0.00 MB"
    assert fmt_bytes(3_500_000) == "3.50 MB" and fmt_bytes(None) == "?"
