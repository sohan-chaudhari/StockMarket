"""Regression: the repeating daily-backfill loop must not run every few minutes.

It is an idempotent backfill, not a latency path, and at a 5-minute cadence it was
the dominant continuous disk reader on the 1 GB box, saturating I/O and stalling
the single event loop (pidstat: ~300-345 kB/s from an otherwise idle worker).
"""
import pathlib
import re

MAIN = pathlib.Path(__file__).resolve().parents[1] / "main.py"
SRC = MAIN.read_text(encoding="utf-8", errors="replace")


def test_interval_constant_is_at_least_30_minutes():
    m = re.search(r"_INDEX_DAILY_BACKFILL_INTERVAL_S\s*=\s*(\d+)", SRC)
    assert m, "backfill interval constant missing"
    assert int(m.group(1)) >= 1800, int(m.group(1))


def test_loop_uses_the_constant_not_a_hardcoded_300():
    assert "max(_INDEX_DAILY_BACKFILL_INTERVAL_S," in SRC
    assert "asyncio.sleep(max(300," not in SRC


def test_backfill_work_is_unchanged():
    # the guarded behaviour must stay: both backfills still run each iteration
    assert "await asyncio.to_thread(_backfill_index_daily)" in SRC
    assert "await asyncio.to_thread(_backfill_daily_from_5m)" in SRC
    assert "_backfill_index_daily" in SRC and "_backfill_daily_from_5m" in SRC