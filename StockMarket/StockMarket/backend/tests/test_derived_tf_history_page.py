"""Regression test: a lazy-load (before=...) page on a DERIVED timeframe
(15m/30m/1h/4h) must return the retained stored rows.

Symptom (reported): opening ETERNAL on 15m showed candles only back to ~4 Sep;
scrolling left never loaded anything older, although the DB holds 15m rows back
to March. Same for every ticker and every derived timeframe.

Cause: in `get_intraday_paginated` the stored rows were discarded
unconditionally --

    if interval in DERIVED_FROM_5M:
        records = []

-- while the rebuild-from-5m path that replaces them is gated on
`is_latest_request`. A history page (before=<epoch> -> is_latest_request False)
therefore returned zero rows and has_more=false, so the client's lazy-load
stopped immediately. Measured on production 2026-10-18:

    ETERNAL 15m before=<epoch> -> rows=0   has_more=false
    ETERNAL  5m before=<epoch> -> rows=600 has_more=true

The discard is correct ONLY for the latest window (stored derived rows are a
subset of what 5m produces, so merging them there left holes); the stored rows
ARE the deep-history tier for everything older.

Driven against an in-memory SQLite `candles` table, so it needs no PostgreSQL.
"""
import datetime
import inspect
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import models


class _StubBackgroundTasks:
    """Non-None so the endpoint takes the queue-a-task path (never the
    synchronous network path) for a latest-window request."""

    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))


def _epoch(y, m, d, hh=0, mm=0):
    """IST wall-clock -> epoch seconds, the same way the endpoint decodes `before`."""
    return int(main._ts_to_epoch(datetime.datetime(y, m, d, hh, mm)))


class DerivedTimeframeHistoryPageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _seed(self, timeframe, stamps):
        db = self.Session()
        try:
            for ts in stamps:
                db.add(models.Candle(
                    ticker="TESTPAGE", timeframe=timeframe, timestamp=ts,
                    open=100.0, high=101.0, low=99.0, close=100.5, volume=1000,
                ))
            db.commit()
        finally:
            db.close()

    def _call(self, **overrides):
        kwargs = dict(ticker="TESTPAGE", interval="15m", before=_epoch(2026, 4, 1),
                      after=None, limit=100, background_tasks=None)
        kwargs.update(overrides)
        db = self.Session()
        try:
            return main.get_intraday_paginated(db=db, **kwargs)
        finally:
            db.close()

    # ---------------------------------------------------------------- the fix
    def test_history_page_returns_stored_derived_rows(self):
        """The reported bug: this returned 0 rows before the fix."""
        self._seed("15m", [datetime.datetime(2026, 3, 6, 9, 15),
                           datetime.datetime(2026, 3, 6, 9, 30),
                           datetime.datetime(2026, 3, 6, 9, 45)])
        res = self._call()
        self.assertEqual(len(res["data"]), 3,
                         "a 15m history page must serve the stored rows")
        self.assertFalse(res["has_more"])
        # ascending order, exactly as the client expects to prepend
        self.assertEqual([c["time"] for c in res["data"]],
                         sorted(c["time"] for c in res["data"]))
        self.assertEqual(res["data"][0]["open"], 100.0)
        self.assertEqual(res["data"][0]["close"], 100.5)

    def test_history_page_is_limited_and_reports_has_more(self):
        """Pagination must still walk backwards a page at a time."""
        stamps = [datetime.datetime(2026, 3, 6, 9, 15) + datetime.timedelta(minutes=15 * i)
                  for i in range(5)]
        self._seed("15m", stamps)
        first = self._call(limit=2)
        self.assertEqual(len(first["data"]), 2, "limit must be honoured")
        self.assertTrue(first["has_more"], "more history exists -> has_more True")

        # walk one page further back and confirm we get older, non-overlapping bars
        oldest = first["data"][0]["time"]
        second = self._call(limit=2, before=oldest)
        self.assertEqual(len(second["data"]), 2)
        self.assertLess(second["data"][-1]["time"], oldest,
                        "the next page must be strictly older")

    def test_all_derived_timeframes_are_covered(self):
        self._seed("30m", [datetime.datetime(2026, 3, 6, 9, 0), datetime.datetime(2026, 3, 6, 9, 30)])
        self._seed("1h", [datetime.datetime(2026, 3, 6, 9, 0), datetime.datetime(2026, 3, 6, 10, 0)])
        self._seed("4h", [datetime.datetime(2026, 3, 6, 9, 0)])
        self.assertEqual(len(self._call(interval="30m")["data"]), 2)
        self.assertEqual(len(self._call(interval="1h")["data"]), 2)
        self.assertEqual(len(self._call(interval="4h")["data"]), 1)

    def test_rows_after_the_cursor_are_excluded(self):
        self._seed("15m", [datetime.datetime(2026, 3, 6, 9, 15),   # before the cursor
                           datetime.datetime(2026, 5, 6, 9, 15)])   # after it (cursor = 1 Apr)
        res = self._call()
        self.assertEqual(len(res["data"]), 1, "before=<epoch> must filter newer rows out")

    # --------------------------------------------------- preserved behaviour
    def test_latest_window_still_discards_stored_derived_rows(self):
        """The documented design (derive the latest window from 5m, not from the
        stored 15m rows) must not regress -- with no 5m rows the latest page for
        a derived timeframe must still come back empty, not from stored 15m."""
        self._seed("15m", [datetime.datetime(2026, 3, 6, 9, 15)])
        res = self._call(before=None, background_tasks=_StubBackgroundTasks())
        self.assertEqual(res["data"], [],
                         "the latest window must still be derived from 5m only")

    def test_5m_is_unaffected(self):
        self._seed("5m", [datetime.datetime(2026, 3, 6, 9, 15)])
        res = self._call(interval="5m")
        self.assertEqual(len(res["data"]), 1, "5m pages already served stored rows")

    def test_latest_page_does_not_raise_for_derived_timeframes(self):
        for tf in ("15m", "30m", "1h", "4h"):
            res = self._call(interval=tf, before=None, background_tasks=_StubBackgroundTasks())
            self.assertIn("data", res)
            self.assertIn("has_more", res)

    # --------------------------------------------------- refactor guard
    def test_discard_is_scoped_to_the_latest_request_in_source(self):
        src = inspect.getsource(main.get_intraday_paginated)
        self.assertIn("if interval in DERIVED_FROM_5M and is_latest_request:", src,
                      "the stored-row discard must stay scoped to the latest window")
        self.assertNotIn("if interval in DERIVED_FROM_5M:\n        records = []", src,
                         "the unconditional discard (which broke lazy-load) must not return")


if __name__ == "__main__":
    unittest.main()
