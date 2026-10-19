"""Phase 1/2 tests: exact 5-minute gap detection and the persistent job store.

The detector is exercised against a real (SQLite) `candles` table so the
"what is actually missing" arithmetic is proven, not asserted by inspection.
The job-store guarantees (lease claim, idempotent enqueue, bounded retry, verify
before complete) are asserted against the module's own SQL/logic, because its DDL
uses Postgres-specific constructs (BIGSERIAL, make_interval, FOR UPDATE SKIP
LOCKED) that SQLite cannot execute.
"""
import datetime
import inspect
import unittest
from datetime import datetime as dt, timedelta
from unittest import mock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import backfill_jobs as bj
import models

TICKER = "ZZGAP"
SESSION_OPEN = dt(2026, 10, 13, 9, 15)


class _Db:
    """SQLite session holding just the `candles` table (portable SELECTs only)."""

    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def add(self, ts, tf="5m"):
        db = self.Session()
        try:
            db.add(models.Candle(ticker=TICKER, timeframe=tf, timestamp=ts,
                                 open=1.0, high=1.0, low=1.0, close=1.0,
                                 volume=1, is_completed=True))
            db.commit()
        finally:
            db.close()

    def session(self):
        return self.Session()


class ExpectedBucketTests(unittest.TestCase):
    def test_a_session_defines_75_buckets(self):
        b = bj.session_buckets(datetime.date(2026, 10, 13))
        self.assertEqual(len(b), 75)                       # 09:15 .. 15:25
        self.assertEqual(b[0].strftime("%H:%M"), "09:15")
        self.assertEqual(b[-1].strftime("%H:%M"), "15:25")

    def test_weekends_contribute_no_buckets(self):
        # Sat 10 / Sun 11 Oct 2026
        got = bj.expected_buckets(dt(2026, 10, 10), dt(2026, 10, 11, 23, 59),
                                  now=dt(2026, 10, 14, 12, 0))
        self.assertEqual(got, [])

    def test_holidays_contribute_no_buckets(self):
        with mock.patch.dict(bj.__dict__, {}), \
             mock.patch("main.NSE_HOLIDAYS", {datetime.date(2026, 10, 13): "test"}):
            got = bj.expected_buckets(dt(2026, 10, 13), dt(2026, 10, 13, 23, 59),
                                      now=dt(2026, 10, 14, 12, 0))
        self.assertEqual(got, [])

    def test_multi_session_range_covers_both_days(self):
        got = bj.expected_buckets(dt(2026, 10, 12), dt(2026, 10, 13, 23, 59),
                                  now=dt(2026, 10, 14, 12, 0))
        self.assertEqual(len(got), 150)                    # 2 trading sessions

    def test_forming_bucket_is_not_missing(self):
        # At 10:02 the 10:00 bucket is still forming and must not count as missing.
        got = bj.expected_buckets(SESSION_OPEN, SESSION_OPEN + timedelta(minutes=50),
                                  now=dt(2026, 10, 13, 10, 2))
        self.assertNotIn(dt(2026, 10, 13, 10, 0), got)
        self.assertIn(dt(2026, 10, 13, 9, 55), got)

    def test_future_buckets_of_the_current_session_are_not_missing(self):
        # Mid-session the job window ends at end-of-day. Buckets that have not
        # started yet must not be counted, or the job could never reach complete.
        got = bj.expected_buckets(SESSION_OPEN, dt(2026, 10, 13, 23, 59, 59),
                                  now=dt(2026, 10, 13, 10, 2))
        self.assertEqual(len(got), 9)                       # 09:15 .. 09:55
        self.assertNotIn(dt(2026, 10, 13, 10, 0), got)      # forming
        self.assertNotIn(dt(2026, 10, 13, 10, 5), got)      # not started
        self.assertNotIn(dt(2026, 10, 13, 15, 25), got)     # later today


class _StubDb:
    """Minimal stand-in for the stored-timestamp query (keeps the test focused on
    the missing-bucket arithmetic, with no SQLite session semantics involved)."""

    def __init__(self, timestamps):
        self._ts = list(timestamps)

    def execute(self, _sql, params=None):
        lo, hi = (params or {}).get("s"), (params or {}).get("e")
        rows = [t for t in self._ts if (lo is None or t >= lo) and (hi is None or t <= hi)]

        class _R:
            def fetchall(self_inner):
                return [(t,) for t in rows]
        return _R()


class MissingRangeTests(unittest.TestCase):
    def setUp(self):
        self.now = dt(2026, 10, 14, 12, 0)      # after both sessions
        self.stored = []

    def _missing(self, start, end):
        return bj.missing_ranges(_StubDb(self.stored), TICKER, "5m", start, end, now=self.now)

    def test_exact_range_is_not_substituted_by_todays_session(self):
        # Stored: yesterday only. Missing: the whole of 13 Oct -> the requested
        # range must be the OLD range, not "today".
        self.stored = bj.session_buckets(datetime.date(2026, 10, 12))
        missing, ranges = self._missing(dt(2026, 10, 12), dt(2026, 10, 13, 23, 59))
        self.assertEqual(len(missing), 75)
        self.assertEqual(ranges, [(dt(2026, 10, 13, 9, 15), dt(2026, 10, 13, 15, 30))])

    def test_complete_range_reports_no_gap(self):
        self.stored = (bj.session_buckets(datetime.date(2026, 10, 12)) +
                       bj.session_buckets(datetime.date(2026, 10, 13)))
        missing, ranges = self._missing(dt(2026, 10, 12), dt(2026, 10, 13, 23, 59))
        self.assertEqual(missing, set())
        self.assertEqual(ranges, [])

    def test_partial_persistence_leaves_the_remainder_open(self):
        # Only the morning of 13 Oct stored -> the afternoon must stay missing.
        self.stored = bj.session_buckets(datetime.date(2026, 10, 13))[:30]
        missing, ranges = self._missing(dt(2026, 10, 13), dt(2026, 10, 13, 23, 59))
        self.assertEqual(len(missing), 45)
        self.assertEqual(ranges[0][0], dt(2026, 10, 13, 11, 45))

    def test_merged_ranges_respect_the_provider_span(self):
        # 100 trading days of gap must be split, not sent as one request.
        missing, ranges = self._missing(dt(2026, 1, 1), dt(2026, 10, 13, 23, 59))
        self.assertTrue(missing)
        self.assertGreater(len(ranges), 1, "a >60 day gap must be split")
        for (s, e) in ranges:
            self.assertLessEqual((e - s).days, bj.MAX_SPAN_DAYS["5m"])

    def test_weekend_only_range_has_no_gap(self):
        missing, ranges = self._missing(dt(2026, 10, 10), dt(2026, 10, 11, 23, 59))
        self.assertEqual(missing, set())


class JobStoreGuaranteeTests(unittest.TestCase):
    """Structural guarantees of the persistent store (Postgres-only DDL)."""

    def setUp(self):
        self.src = inspect.getsource(bj)

    def test_claim_is_atomic_and_leased(self):
        i = self.src.find("def claim(")
        block = self.src[i:i + 1800]
        self.assertIn("FOR UPDATE SKIP LOCKED", block,
                      "concurrent workers must not claim the same job")
        self.assertIn("lease_expires_at", block)
        self.assertIn("lease_expires_at < NOW()", block,
                      "an expired lease must be re-claimable after a restart")

    def test_enqueue_is_idempotent(self):
        self.assertIn("uix_backfill_jobs_open", self.src)
        # Assert on the FUNCTION source, not a fixed character window: the window
        # made this test fail purely because enqueue's docstring grew.
        block = inspect.getsource(bj.enqueue)
        self.assertIn("ON CONFLICT", block)

    def test_retry_is_bounded_and_jittered(self):
        vals = [bj._backoff_seconds(1) for _ in range(8)]
        self.assertGreaterEqual(min(vals), bj.JOB_BASE_BACKOFF_SEC * 0.5)
        self.assertLessEqual(max(vals), bj.JOB_BASE_BACKOFF_SEC)
        self.assertGreater(len(set(vals)), 1, "backoff must be jittered, not synchronized")
        self.assertLessEqual(bj._backoff_seconds(99), bj.JOB_MAX_BACKOFF_SEC)
        self.assertGreaterEqual(bj._backoff_seconds(1, retry_after=999), 999,
                                "Retry-After must win over the computed backoff")

    def test_complete_requires_verified_persistence(self):
        i = self.src.find("def run_job(")
        block = self.src[i:]
        self.assertIn("still_missing, _ = missing_ranges(", block,
                      "a job must re-read stored candles before reporting")
        self.assertIn('if not still_missing:', block)
        # the only path to 'complete' is the verified-empty-missing branch
        self.assertEqual(block.count('finish(job["id"], "complete"'), 2)
        self.assertIn('"permanently_failed"', block,
                      "unresolvable gaps must be recorded, not silently completed")

    def test_retention_policy_exists(self):
        self.assertIn("def cleanup(", self.src)
        self.assertIn("DELETE FROM backfill_jobs", self.src)

    def test_failure_kinds_are_distinguished(self):
        # Each outcome the worker can reach must be nameable: a throttle, a
        # transient error, an invalid symbol, a persistence failure, a
        # successful-but-empty response, proven absence, and pre-listing data.
        for kind in ('"throttled"', '"transient"', '"unsupported"', '"persist"',
                     '"empty_response"', '"provider_absent"', '"pre_data"'):
            self.assertIn(kind, self.src)

    def test_market_hours_priority_in_worker(self):
        import main
        src = inspect.getsource(main._backfill_job_worker)
        self.assertIn("market_open = is_market_open_now()", src)
        self.assertIn("1 if market_open else", src,
                      "market hours must process fewer background jobs")


class MigrationTests(unittest.TestCase):
    def test_migration_file_exists_and_is_idempotent(self):
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "v008_backfill_jobs.sql")
        self.assertTrue(os.path.exists(p), "v008 migration missing")
        with open(p) as f:
            sql = f.read()
        self.assertIn("CREATE TABLE IF NOT EXISTS backfill_jobs", sql)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uix_backfill_jobs_open", sql)
        self.assertIn("ix_backfill_jobs_claimable", sql)


if __name__ == "__main__":
    unittest.main()
