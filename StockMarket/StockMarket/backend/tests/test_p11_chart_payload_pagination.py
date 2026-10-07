"""P1.1 — Chart payload/pagination regression tests (backend).

Proves the intraday chart endpoint honours an explicit page size and that
stepping back through history with `before=` yields strictly older, non-duplicated
bars with correct OHLCV — i.e. that reducing the initial page never removes
historical depth, it only moves it behind lazy-load.

Uses the real configured engine/SessionLocal (same pattern as
test_intraday_paginated_session_lifetime.py) because the point is the real query
path. Writes only to one unique, obviously-fake ticker, deleted in setUp/tearDown.
"""
import unittest
from datetime import datetime, timedelta

import database
import models
import main

TEST_TICKER = "ZZTESTP11CHART"
BAR = timedelta(minutes=5)
PAGE = 600
TOTAL = 700


def _postgres_reachable():
    try:
        conn = database.engine.connect()
        conn.close()
        return True
    except Exception:
        return False


POSTGRES_AVAILABLE = _postgres_reachable()


def _delete(ticker):
    db = database.SessionLocal()
    try:
        db.query(models.Candle).filter(models.Candle.ticker == ticker).delete()
        db.commit()
    finally:
        db.close()


def _seed(ticker, count):
    db = database.SessionLocal()
    try:
        base = datetime(2026, 3, 2, 9, 15, 0)
        for i in range(count):
            px = 100.0 + i * 0.01
            db.add(models.Candle(
                ticker=ticker, timeframe="5m", timestamp=base + i * BAR,
                open=round(px, 2), high=round(px + 0.5, 2), low=round(px - 0.5, 2),
                close=round(px + 0.2, 2), volume=1000 + i, is_completed=True,
            ))
        db.commit()
    finally:
        db.close()


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class ChartPayloadPaginationTests(unittest.TestCase):
    def setUp(self):
        _delete(TEST_TICKER)
        _seed(TEST_TICKER, TOTAL)
        # The endpoint's "latest request" path can trigger a network gap-fill;
        # neutralise it so these tests stay pure DB/query assertions.
        self._bf = main.perform_on_demand_backfill
        main.perform_on_demand_backfill = lambda *a, **k: None

    def tearDown(self):
        main.perform_on_demand_backfill = self._bf
        _delete(TEST_TICKER)

    def _call(self, **overrides):
        kwargs = dict(ticker=TEST_TICKER, interval="5m", before=None, after=None,
                      limit=PAGE, background_tasks=None, db=database.SessionLocal())
        kwargs.update(overrides)
        try:
            return main.get_intraday_paginated(**kwargs)
        finally:
            kwargs["db"].close()

    def test_limit_is_respected(self):
        res = self._call()
        self.assertLessEqual(len(res["data"]), PAGE)
        self.assertEqual(len(res["data"]), PAGE)
        self.assertTrue(res["has_more"])

    def test_chronological_order_and_no_duplicates(self):
        data = self._call()["data"]
        times = [c["time"] for c in data]
        self.assertEqual(times, sorted(times), "bars must be in ascending time order")
        self.assertEqual(len(times), len(set(times)), "bars must not contain duplicate timestamps")

    def test_ohlcv_values_and_schema_intact(self):
        data = self._call()["data"]
        for c in data:
            self.assertEqual(set(c.keys()), {"time", "open", "high", "low", "close", "volume"})
            self.assertGreater(c["open"], 0)
            self.assertGreater(c["high"], 0)
            self.assertLessEqual(c["low"], c["high"])
            self.assertIsInstance(c["time"], int)
        # The window must end on the newest candle (desc query, reversed).
        newest = max(c["time"] for c in data)
        self.assertEqual(data[-1]["time"], newest)

    def test_lazy_load_cursor_returns_strictly_older_non_overlapping_bars(self):
        first = self._call()
        oldest_ts = first["data"][0]["time"]

        older = self._call(before=oldest_ts)
        self.assertTrue(older["data"], "stepping back must return older history")
        self.assertEqual(older["has_more"], False, "700 bars / 600-page => one older page left")
        older_times = [c["time"] for c in older["data"]]
        self.assertTrue(all(t < oldest_ts for t in older_times),
                        "lazy-load must return only bars strictly older than the cursor")
        # No overlap between the two pages => prepending cannot duplicate timestamps.
        self.assertEqual(set(older_times) & {c["time"] for c in first["data"]}, set())
        self.assertEqual(len(first["data"]) + len(older["data"]), TOTAL)

    def test_total_history_accessible_via_pagination(self):
        seen = set()
        cursor = None
        for _ in range(10):
            page = self._call(before=cursor) if cursor is not None else self._call()
            if not page["data"]:
                break
            for c in page["data"]:
                seen.add(c["time"])
            if not page["has_more"]:
                break
            cursor = page["data"][0]["time"]
        self.assertEqual(len(seen), TOTAL,
                         "all seeded history must remain reachable through pagination")


if __name__ == "__main__":
    unittest.main()
