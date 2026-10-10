"""Phase 2 (A3) regression: /api/stock-data/intraday/paginated history-depth
detection.

A ticker whose stored intraday history does not reach back to its timeframe's
supported window (the same window `_intraday_cutoff()` queries, mirroring the
retention tiers) is missing deep history. The tip/internal gap checks only run
while the market is OPEN, so such a ticker was never backfilled when viewed
off-hours -- the audit's "sparse history, market closed, never backfills" case.

The endpoint now detects the depth gap with a bounded, index-backed query (the
oldest stored row, NOT the last element of the `limit`-capped main query) and
dispatches a fill bounded to that timeframe's window. `perform_on_demand_backfill`
no longer suppresses a DEEP fill off-hours: its closed-market guard is scoped to
shallow/tip fills. All of this runs against in-memory SQLite -- no production or
external data is touched.
"""
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
import main
from database import Base


def _mk_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _Recorder:
    """Stands in for FastAPI's BackgroundTasks.add_task."""

    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))


class IntradayHistoryDepthTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_session()
        main._backfill_cooldown.clear()

    def tearDown(self):
        main._backfill_cooldown.clear()
        self.db.close()

    def _seed_5m(self, ticker, start, count):
        for i in range(count):
            ts = start + timedelta(minutes=5 * i)
            self.db.add(models.Candle(
                ticker=ticker, timeframe="5m", timestamp=ts,
                open=100.0, high=101.0, low=99.0, close=100.5, volume=10,
                is_completed=True,
            ))
        self.db.commit()

    def _call(self, ticker, interval="5m", recorder=None):
        return main.get_intraday_paginated(
            ticker=ticker, interval=interval, before=None, after=None,
            limit=1500, background_tasks=recorder, db=self.db,
        )

    def test_sparse_recent_history_triggers_depth_fill(self):
        tk = "ZZDEPTH5M"
        now = main.database.get_ist_now()
        # only a few recent candles -- all well inside the 65-day 5m window, no
        # tip gap (the newest is "now"), no same-day internal gap.
        self._seed_5m(tk, now - timedelta(minutes=25), 6)
        rec = _Recorder()
        self._call(tk, "5m", rec)
        self.assertTrue(rec.tasks, "a sparse ticker must dispatch a history-depth fill")
        _fn, args, _kw = rec.tasks[0]
        self.assertEqual(args[0], tk)
        self.assertEqual(args[1], "5m")
        # fill_start is the timeframe window, not a blanket "now - 60 days".
        expected = main._intraday_cutoff("5m")
        self.assertLessEqual(abs((args[2] - expected).total_seconds()), 1)

    def test_deep_history_does_not_trigger_fill(self):
        tk = "ZZDEEPOK"
        now = main.database.get_ist_now()
        # oldest candle at the very start of the 5m window -> depth satisfied
        self._seed_5m(tk, main._intraday_cutoff("5m") - timedelta(days=3), 6)
        # ...plus a fresh tip so there is no tip gap either
        self._seed_5m(tk, now - timedelta(minutes=10), 3)
        rec = _Recorder()
        self._call(tk, "5m", rec)
        self.assertEqual(
            rec.tasks, [],
            "a ticker whose history already reaches the window floor must not be re-filled",
        )

    def test_depth_fill_respects_cooldown(self):
        tk = "ZZDEPTHCD"
        now = main.database.get_ist_now()
        self._seed_5m(tk, now - timedelta(minutes=25), 6)
        main._set_backfill_cooldown(f"{tk}:5m", 300)
        rec = _Recorder()
        self._call(tk, "5m", rec)
        self.assertEqual(rec.tasks, [], "an active cooldown must suppress the depth fill")

    def test_per_timeframe_window_is_used(self):
        # 1m must bound to its own (7-day) window, not the 5m window.
        tk = "ZZDEPTH1M"
        now = main.database.get_ist_now()
        self._seed_5m(tk, now - timedelta(minutes=5), 3)
        # re-seed the same ticker under the 1m timeframe
        for i in range(3):
            self.db.add(models.Candle(
                ticker=tk, timeframe="1m", timestamp=now - timedelta(minutes=3 - i),
                open=100.0, high=101.0, low=99.0, close=100.5, volume=10,
                is_completed=True,
            ))
        self.db.commit()
        rec = _Recorder()
        self._call(tk, "1m", rec)
        self.assertTrue(rec.tasks, "sparse 1m history must dispatch a fill")
        _fn, args, _kw = rec.tasks[0]
        expected = main._intraday_cutoff("1m")
        self.assertLessEqual(abs((args[2] - expected).total_seconds()), 1)
        # the 1m window (7d) is more recent than the 5m window (65d) -- proving
        # the per-timeframe bound is used, not a single blanket window.
        self.assertGreater(args[2], main._intraday_cutoff("5m"))


class ClosedMarketDeepFillTests(unittest.TestCase):
    """The closed-market suppression must not swallow a DEEP-history fill."""

    def test_shallow_fill_is_suppressed_when_closed(self):
        import inspect
        src = inspect.getsource(main.perform_on_demand_backfill)
        self.assertIn("shallow_fill = backfill_start >= (now - timedelta(days=5))", src)
        self.assertIn("if not is_market_open_now() and shallow_fill:", src)


if __name__ == "__main__":
    unittest.main()
