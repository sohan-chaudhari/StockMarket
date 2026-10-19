"""CRIT-02 regression: the intraday gap decision for DERIVED timeframes.

Reported: opening a chart on 5m showed a gap; switching to 15m appeared to
"backfill" it; switching back to 5m showed no gap.

What was actually happening (measured on production):

1. A derived timeframe (15m/30m/1h/4h) is SERVED from stored 5m -- its own stored
   rows are a retired legacy artifact and are discarded by the builder. The gap
   checks, however, were run against those discarded rows, whose stored copy
   simply stops at an older date. So EVERY 15m/30m/1h/4h open logged
   "Entire today session missing" / "Sync fill needed ... gap from <old date>"
   and dispatched `perform_on_demand_backfill` for the derived interval.
2. That dispatch provably cannot repair anything: for a derived interval the
   function never fetches from a provider and never persists (its own comment:
   "Derived timeframes end here"), and for an INDEX it even ran a yfinance
   download that was then thrown away. Pure overhead + provider/budget churn.
3. The 5m repair that actually matters was only ever scheduled from a check that
   sat INSIDE the `else:` (non-empty stored rows) branch -- so a derived timeframe
   holding no stored rows of its own never queued a 5m repair at all.

The fix judges derived timeframes by their real source (5m), hoists that check so
it always runs, and never dispatches a fill for a derived interval. `fill_needed`
is still set from the 5m state, because that is what the response reports as
`backfill_status` and what the client follows.

Driven against in-memory SQLite with a stubbed `backfill_jobs`, so it needs no
PostgreSQL and touches no provider.
"""
import datetime
import unittest
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import models


class _StubBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))


class _StubBackfillJobs:
    """Minimal stand-in so the decision path runs without the job tables."""

    def __init__(self, missing=True, enqueue_returns=True):
        self.missing = missing
        self.enqueue_returns = enqueue_returns
        self.enqueue_calls = []
        self.has_missing_calls = []

    def has_missing(self, db, ticker, interval, start, end):
        self.has_missing_calls.append((ticker, interval))
        return self.missing

    def enqueue(self, ticker, interval, start, end, reason=None):
        self.enqueue_calls.append((ticker, interval, reason))
        return self.enqueue_returns

    def missing_ranges(self, db, ticker, interval, start, end, now=None):
        return ([], [])

    def has_open_job(self, ticker, interval):
        return False


class DerivedTimeframeGapDecisionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _seed_5m(self, days=3):
        db = self.Session()
        try:
            for d in range(days):
                day = datetime.datetime(2026, 10, 13) + datetime.timedelta(days=d)
                for b in range(4):
                    db.add(models.Candle(
                        ticker="TESTGAP", timeframe="5m",
                        timestamp=day + datetime.timedelta(minutes=5 * b),
                        open=100.0, high=101.0, low=99.0, close=100.5, volume=100,
                    ))
            db.commit()
        finally:
            db.close()

    def _run(self, interval, missing=True):
        """Call the endpoint with a stubbed backfill_jobs module."""
        import sys
        stub = _StubBackfillJobs(missing=missing)
        saved = sys.modules.get("backfill_jobs")
        sys.modules["backfill_jobs"] = stub
        self.bg = _StubBackgroundTasks()
        db = self.Session()
        try:
            res = main.get_intraday_paginated(
                ticker="TESTGAP", interval=interval, before=None, after=None,
                limit=600, background_tasks=self.bg, db=db)
        finally:
            db.close()
            if saved is not None:
                sys.modules["backfill_jobs"] = saved
            else:
                sys.modules.pop("backfill_jobs", None)
        return res, stub

    # ----------------------------------------------------------- the contract
    def test_derived_timeframe_never_dispatches_a_provider_fill(self):
        """A derived interval can neither fetch nor persist, so nothing may be
        dispatched for it -- that dispatch was pure overhead on every open."""
        self._seed_5m()
        res, stub = self._run("15m")
        dispatched = [t for t in self.bg.tasks if t[1] and t[1][1] in ("15m", "30m", "1h", "4h")]
        self.assertEqual(dispatched, [],
                         "no perform_on_demand_backfill may be dispatched for a derived timeframe")

    def test_derived_timeframe_still_queues_the_5m_repair(self):
        """5m is the source of truth: the repair that matters must still be queued."""
        self._seed_5m()
        res, stub = self._run("15m")
        self.assertTrue(any(a[1] == "5m" for a in stub.enqueue_calls),
                        "the 5m repair must still be enqueued for a 15m chart")

    def test_derived_timeframe_with_no_stored_rows_of_its_own_still_queues_5m(self):
        """The old placement of the check inside `else:` meant a derived timeframe
        with no stored rows of its own queued nothing at all."""
        self._seed_5m()          # 5m only: no 15m rows exist
        res, stub = self._run("15m")
        self.assertTrue(any(a[1] == "5m" for a in stub.enqueue_calls),
                        "the 5m repair must be queued even with no stored 15m rows")

    def test_status_follows_the_5m_source(self):
        """backfill_status is what the client follows, so it must describe 5m."""
        self._seed_5m()
        res, stub = self._run("15m", missing=True)
        self.assertEqual(res["backfill_status"], "backfilling",
                         "with 5m missing, the status must say backfilling")
        res2, _ = self._run("15m", missing=False)
        self.assertEqual(res2["backfill_status"], "idle",
                         "with 5m complete, a 15m open must NOT claim backfilling")

    def test_5m_still_reports_and_queues(self):
        """The non-derived path must keep working exactly as before."""
        self._seed_5m()
        res, stub = self._run("5m", missing=True)
        self.assertEqual(res["backfill_status"], "backfilling")
        self.assertTrue(any(a[1] == "5m" for a in stub.enqueue_calls))

    # ------------------------------------------------- coverage of every derived TF
    def test_all_derived_timeframes_covered(self):
        self._seed_5m()
        for tf in ("15m", "30m", "1h", "4h"):
            self.bg = None
            res, stub = self._run(tf)
            wrong = [t for t in self.bg.tasks if t[1] and t[1][1] == tf]
            self.assertEqual(wrong, [], f"{tf} must not dispatch a fill for itself")
            self.assertTrue(any(a[1] == "5m" for a in stub.enqueue_calls), f"{tf} must queue the 5m repair")

    # ------------------------------------------------------------- source guards
    def test_decisions_are_sourced_from_5m_not_the_discarded_rows(self):
        import inspect
        src = inspect.getsource(main.get_intraday_paginated)
        i_src = src.find("_src_missing = _bj.has_missing")
        i_derived = src.find("if interval in DERIVED_FROM_5M:")
        self.assertNotEqual(i_src, -1, "the 5m source check must exist")
        self.assertNotEqual(i_derived, -1, "the derived branch must exist")
        self.assertLess(i_src, i_derived,
                        "the 5m source check must run BEFORE the derived decision")
        self.assertIn("fill_needed = _src_missing", src,
                      "the derived decision must come from the 5m source")
        self.assertIn("if fill_needed and interval not in DERIVED_FROM_5M:", src,
                      "the dispatch must be skipped for derived timeframes")
        self.assertIn("if not fill_needed and interval not in DERIVED_FROM_5M:", src,
                      "the history-depth check must not run on the discarded rows either")


if __name__ == "__main__":
    unittest.main()
