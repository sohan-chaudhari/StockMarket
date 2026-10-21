"""Regression: an empty provider response must never park a ticker permanently.

DRREDDY ended at complete_with_gaps / provider_empty with 38 buckets unfilled after
7 attempts; because that is terminal, opening the ticker could never fill the gap.
A response that persisted NOTHING is not evidence the data does not exist, so the job
stays retryable at the capped backoff (bounded provider load).
"""
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1] / "backfill_jobs.py").read_text(
    encoding="utf-8", errors="replace")


def test_no_rows_persisted_is_not_terminal():
    i = SRC.index('if job["attempts"] >= JOB_MAX_ATTEMPTS and kind != "auth":')
    block = SRC[i:i + 2200]
    assert 'if persisted > 0:' in block, "terminal branch must require real progress"
    assert "provider_empty_retry" in block, "empty responses must stay retryable"


def test_retry_uses_the_capped_backoff():
    i = SRC.index("provider_empty_retry")
    block = SRC[max(0, i - 1200):i]
    assert "finish(job[\"id\"], \"retry_wait\"" in block
    assert "backoff_override=JOB_MAX_BACKOFF_SEC" in block, "must not retry fast"


def test_progress_still_finalises_honestly():
    i = SRC.index('if job["attempts"] >= JOB_MAX_ATTEMPTS and kind != "auth":')
    block = SRC[i:i + 2200]
    assert 'finish(job["id"], "complete_with_gaps"' in block, "partial progress must still close honestly"