"""Regression: a job stuck in `running` must be reclaimed, not block its ticker.

Observed live: BAFNAPH 5m sat in `running` for 40 minutes. While it is running,
enqueue refuses new work for that (ticker, interval), so the repair never happens.
"""
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1] / "backfill_jobs.py").read_text(
    encoding="utf-8", errors="replace")


def test_reaper_exists_and_targets_only_stuck_running_rows():
    assert "def reap_stale(" in SRC
    i = SRC.index("def reap_stale(")
    body = SRC[i:i + 1600]
    assert "WHERE status = 'running'" in body, "must only touch running rows"
    assert "last_attempt_at" in body and "make_interval" in body
    assert "status = 'retry_wait'" in body
    assert "INTERVAL '120 seconds'" in body, "reclaimed jobs get a short bounded backoff"


def test_reaper_is_called_from_the_batch_loop():
    i = SRC.index("def run_batch(")
    body = SRC[i:i + 400]
    assert "reap_stale()" in body, "the batch loop must reap before claiming"


def test_reaper_never_touches_finished_work():
    i = SRC.index("def reap_stale(")
    body = SRC[i:i + 1600]
    for terminal in ("complete_with_gaps", "permanently_failed"):
        assert terminal not in body, "reaper must not rewrite terminal states"