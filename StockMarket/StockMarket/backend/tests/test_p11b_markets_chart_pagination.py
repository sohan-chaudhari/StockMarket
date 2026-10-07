"""P1.1b — Markets chart pagination regression test (backend).

Proves the exact request sequence market.html now issues — initial page
(limit=600, before=None) followed by lazy 200-bar pages stepping backwards —
still reaches ALL seeded history with correct order, no duplicates, and intact
OHLCV. The endpoint itself is unchanged by P1.1b; this locks the Markets flow.
"""
import unittest
from datetime import datetime, timedelta

import database
import models
import main

TEST_TICKER = "ZZTESTP11BMKT"
BAR = timedelta(minutes=5)
INITIAL = 600
LAZY = 200
TOTAL = 1000


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
        base = datetime(2026, 2, 2, 9, 15, 0)
        for i in range(count):
            px = 50.0 + i * 0.02
            db.add(models.Candle(
                ticker=ticker, timeframe="5m", timestamp=base + i * BAR,
                open=round(px, 2), high=round(px + 0.4, 2), low=round(px - 0.4, 2),
                close=round(px + 0.1, 2), volume=500 + i, is_completed=True,
            ))
        db.commit()
    finally:
        db.close()


def _newest_row(ticker):
    db = database.SessionLocal()
    try:
        # Mirror get_intraday_paginated's own predicate (ticker + timeframe) so the
        # composite index is used; a ticker-only ORDER BY can exceed statement_timeout.
        return (db.query(models.Candle)
                .filter(models.Candle.ticker == ticker, models.Candle.timeframe == "5m")
                .order_by(models.Candle.timestamp.desc()).first())
    finally:
        db.close()


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class MarketsChartPaginationTests(unittest.TestCase):
    def setUp(self):
        _delete(TEST_TICKER)
        _seed(TEST_TICKER, TOTAL)
        self._bf = main.perform_on_demand_backfill
        main.perform_on_demand_backfill = lambda *a, **k: None

    def tearDown(self):
        main.perform_on_demand_backfill = self._bf
        _delete(TEST_TICKER)

    def _call(self, **overrides):
        kwargs = dict(ticker=TEST_TICKER, interval="5m", before=None, after=None,
                      limit=INITIAL, background_tasks=None, db=database.SessionLocal())
        kwargs.update(overrides)
        try:
            return main.get_intraday_paginated(**kwargs)
        finally:
            kwargs["db"].close()

    def test_initial_page_is_bounded_ordered_and_ends_on_newest(self):
        page = self._call()
        self.assertEqual(len(page["data"]), INITIAL)
        self.assertTrue(page["has_more"])
        times = [c["time"] for c in page["data"]]
        self.assertEqual(times, sorted(times), "ascending order")
        self.assertEqual(len(times), len(set(times)), "no duplicate timestamps")
        self.assertEqual(page["data"][-1]["time"], int(_newest_row(TEST_TICKER).timestamp.timestamp()))

    def test_initial_plus_lazy_pages_reach_all_history_without_gaps_or_dupes(self):
        first = self._call()
        seen = {c["time"] for c in first["data"]}
        self.assertTrue(first["has_more"])
        cursor = first["data"][0]["time"]

        pages = 0
        while cursor is not None and pages < 20:
            page = self._call(before=cursor, limit=LAZY)
            pages += 1
            if not page["data"]:
                break
            times = [c["time"] for c in page["data"]]
            self.assertTrue(all(t < cursor for t in times), "lazy page must be strictly older than the cursor")
            self.assertEqual(set(times) & seen, set(), "pages must not overlap")
            seen.update(times)
            if not page["has_more"]:
                break
            cursor = page["data"][0]["time"]

        self.assertEqual(len(seen), TOTAL, "all seeded history must remain reachable via initial+200-bar lazy pages")

    def test_ohlcv_sample_intact(self):
        row = _newest_row(TEST_TICKER)
        newest = self._call()["data"][-1]
        self.assertAlmostEqual(newest["open"], float(row.open), places=2)
        self.assertAlmostEqual(newest["high"], float(row.high), places=2)
        self.assertAlmostEqual(newest["low"], float(row.low), places=2)
        self.assertAlmostEqual(newest["close"], float(row.close), places=2)


if __name__ == "__main__":
    unittest.main()
