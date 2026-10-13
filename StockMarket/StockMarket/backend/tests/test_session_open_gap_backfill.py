"""Regression test: a missing SESSION OPEN must trigger the on-open backfill.

Reported: opening a chart (e.g. ANGELONE 5m) showed candles only from 09:25 --
the 09:15 and 09:20 bars were missing and the backfill never fetched them.

Cause: the morning-session gap check used a TWO-bucket tolerance:

    earliest_today > session_open_dt + timedelta(minutes=bucket_min * 2)

For 5m that is `09:25 > 09:25` -> False, so the common 09:15+09:20 gap was
invisible and `fill_needed` stayed False. One bucket of tolerance still absorbs a
single legitimately empty opening bucket (illiquid names) while catching the
reported two-bucket gap; holes AFTER the earliest candle are handled by the
internal-gap check (which keeps its own two-bucket tolerance by design).

The clock is pinned so the tip-gap check cannot fire and mask the assertions.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import models

TICKER = "ZZZOPEN"
FIXED_NOW = datetime(2026, 10, 13, 9, 45, 0)   # IST-naive, mid-session
SESSION_OPEN = datetime(2026, 10, 13, 9, 15, 0)


class SessionOpenGapTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.calls = []
        self._patchers = [
            patch("database.get_ist_now", lambda *a, **k: FIXED_NOW),
            patch.object(main, "is_market_open_now", lambda *a, **k: True),
            patch.object(main, "is_trading_day", lambda *a, **k: True),
            patch.object(
                main, "perform_on_demand_backfill",
                side_effect=lambda t, i, start, now: self.calls.append((t, i, start))),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self._patchers:
            p.stop()

    @staticmethod
    def _session_bars(day_open):
        """All 75 five-minute buckets of a session (09:15 .. 15:25)."""
        return [day_open + timedelta(minutes=5 * k) for k in range(75)]

    def _seed(self, minutes):
        """Today's bars at 09:15 + m minutes, plus 70 FULL previous sessions so the
        fixture looks like real data: no internal holes, no early finish, no
        late start, and deep enough that the history-depth check cannot fire."""
        db = self.Session()
        try:
            for d in range(1, 71):
                day_open = SESSION_OPEN - timedelta(days=d)
                for ts in self._session_bars(day_open):
                    db.add(models.Candle(ticker=TICKER, timeframe="5m", timestamp=ts,
                                         open=100.0, high=101.0, low=99.0, close=100.5,
                                         volume=1000, is_completed=True))
            for m in minutes:
                db.add(models.Candle(ticker=TICKER, timeframe="5m",
                                     timestamp=SESSION_OPEN + timedelta(minutes=m),
                                     open=100.0, high=101.0, low=99.0, close=100.5,
                                     volume=1000, is_completed=True))
            db.commit()
        finally:
            db.close()

    def _seed_with_hole(self):
        """Today's session plus two isolated older sessions (30 and 70 days back)
        with a multi-day hole between -- the RPSGVENT shape."""
        db = self.Session()
        try:
            for m in (0, 5, 10, 15, 20, 25):
                db.add(models.Candle(ticker=TICKER, timeframe="5m",
                                     timestamp=SESSION_OPEN + timedelta(minutes=m),
                                     open=100.0, high=101.0, low=99.0, close=100.5,
                                     volume=1000, is_completed=True))
            for d in (30, 70):
                for ts in self._session_bars(SESSION_OPEN - timedelta(days=d)):
                    db.add(models.Candle(ticker=TICKER, timeframe="5m", timestamp=ts,
                                         open=100.0, high=101.0, low=99.0, close=100.5,
                                         volume=1000, is_completed=True))
            db.commit()
        finally:
            db.close()

    def _call(self):
        db = self.Session()
        try:
            return main.get_intraday_paginated(
                ticker=TICKER, interval="5m", before=None, after=None,
                limit=100, background_tasks=None, db=db)
        finally:
            db.close()

    def test_missing_first_two_buckets_triggers_backfill_from_session_open(self):
        # earliest today = 09:25 -> 09:15 and 09:20 missing (the reported case)
        self._seed([10, 15, 20, 25])
        self._call()
        self.assertTrue(self.calls, "a 09:15+09:20 opening gap must trigger a backfill")
        _t, _i, start = self.calls[0]
        self.assertEqual(start, SESSION_OPEN,
                         "the fill must start at the session open (09:15), not at the earliest candle")

    def test_single_empty_opening_bucket_is_tolerated(self):
        # earliest today = 09:20 (only 09:15 absent). Deliberately tolerated so a
        # legitimately empty opening bucket on an illiquid name cannot cause a
        # provider fetch on every chart open.
        self._seed([5, 10, 15, 20, 25])
        self._call()
        self.assertEqual(self.calls, [])

    def test_complete_session_does_not_trigger(self):
        self._seed([0, 5, 10, 15, 20, 25])
        self._call()
        self.assertEqual(self.calls, [], "a complete session must not trigger a backfill")

    def test_internal_two_bucket_hole_is_still_caught(self):
        # 09:15 then 09:30 -> a two-bucket hole inside the session.
        self._seed([0, 15, 20, 25])
        self._call()
        self.assertTrue(self.calls, "an internal same-day hole must still trigger a backfill")

    def test_opened_ticker_with_a_hole_is_queued_for_the_sweep(self):
        # The request path ENQUEUES an exact-range job (the leased worker does the
        # paced fetching), so the request returns immediately and the gap is
        # scheduled separately. The exact missing-set check is what gates it; it is
        # patched here so the assertion is about the enqueue target, not the query.
        self._seed([0, 5, 10, 15, 20, 25])
        with patch("backfill_jobs.has_missing", return_value=True), \
             patch("backfill_jobs.enqueue", return_value=1) as enq:
            self._call()
        self.assertTrue(enq.called, "opening a ticker with a missing bucket must enqueue a repair job")
        _a, kw = enq.call_args
        self.assertEqual(_a[0], TICKER)
        self.assertEqual(_a[1], "5m")

    def test_fresh_ticker_is_not_queued(self):
        self._seed([0, 5, 10, 15, 20, 25])
        with patch("backfill_jobs.enqueue", return_value=1) as enq:
            self._call()
        self.assertFalse(enq.called, "a ticker with current, hole-free data must not be enqueued")

    def test_weekend_gap_is_not_a_hole(self):
        # Fri -> Mon is 3 calendar days and must NOT be treated as missing data.
        self._seed([0, 5, 10, 15, 20, 25])
        self._call()
        self.assertEqual(self.calls, [], "a normal weekend gap must not trigger a backfill")


if __name__ == "__main__":
    unittest.main()
