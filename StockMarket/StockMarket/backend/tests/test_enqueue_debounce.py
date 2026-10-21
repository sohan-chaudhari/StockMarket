"""Regression: a ticker must not be re-queued immediately after its job finished.

Live: eight identical `[BackfillJob] queued MASTEK 5m` lines in 90 seconds, each one
another provider fetch, while chart requests took 4-5 s behind them.
"""
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1] / "backfill_jobs.py").read_text(
    encoding="utf-8", errors="replace")


def test_debounce_exists_inside_enqueue():
    i = SRC.index("def enqueue(")
    body = SRC[i:i + 2500]
    assert "RECENT_ENQUEUE_SUPPRESS_MINUTES" in body, "debounce window missing from enqueue"
    assert "status IN ('complete','complete_with_gaps','permanently_failed')" in body, \
        "only finished jobs may suppress a new enqueue"
    assert "return _recent[0]" in body, "a suppressed enqueue reuses the existing job id"


def test_debounce_is_bounded_and_configurable():
    assert "BACKFILL_RECENT_ENQUEUE_MINUTES" in SRC
    i = SRC.index("RECENT_ENQUEUE_SUPPRESS_MINUTES = int(")
    assert 'os.getenv("BACKFILL_RECENT_ENQUEUE_MINUTES", "10")' in SRC[i:i + 120]


def test_open_jobs_still_deduplicate_as_before():
    # the ON CONFLICT path for live jobs must remain untouched
    assert "WHERE status IN ('queued','running','retry_wait','partially_complete')" in SRC