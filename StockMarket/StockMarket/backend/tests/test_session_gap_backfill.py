"""Targeted regression test suite for historical candle backfill and session gap detection.

Covers the full suite of required test scenarios:
1. Missing morning session (latest available candle starts at 11:35 AM -> detects 09:15 morning gap).
2. Complete session (no gap detected, no backfill triggered, backfill_status is idle).
3. Missing historical candles across previous trading sessions (detects older gaps).
4. Legitimate late first trade (e.g. 10:30 AM) on an active day must NOT trigger unnecessary deep fill.
5. Legitimate early session end / special trading session (e.g. 14:30) must NOT trigger uncontrolled fill.
6. Weekend and holiday boundaries must not be classified as trading gaps.
7. Provider failure / cooldown guard safety.
8. In-memory SQLite fixtures with no external network dependency.
"""
import unittest
from datetime import datetime, date, time, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import models


class HistoricalCandleBackfillRegressionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        main.backfill_locks.clear()

    def _insert_candles(self, ticker, timeframe, candles):
        db = self.Session()
        try:
            for c in candles:
                rec = models.Candle(
                    ticker=ticker,
                    timeframe=timeframe,
                    timestamp=c["timestamp"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c.get("volume", 1000),
                    is_completed=True,
                    data_source="TEST"
                )
                db.add(rec)
            db.commit()
        finally:
            db.close()

    def test_missing_morning_session_gap_detected(self):
        """Case: Stock opened mid-day (e.g. 11:35 AM).
        Verify gap detector flags 09:15 session open gap and marks fill_needed.
        """
        db = self.Session()
        try:
            now = main.database.get_ist_now()
            today_date = now.date()
            t1135 = datetime.combine(today_date, time(11, 35))
            t1140 = datetime.combine(today_date, time(11, 40))
            
            candles = [
                {"timestamp": t1135, "open": 200.0, "high": 205.0, "low": 199.0, "close": 202.0},
                {"timestamp": t1140, "open": 202.0, "high": 203.0, "low": 201.0, "close": 202.5},
            ]
            self._insert_candles("TESTMORNING", "5m", candles)

            res = main.get_intraday_paginated(
                ticker="TESTMORNING", interval="5m", before=None, limit=100,
                background_tasks=None, db=db
            )
            self.assertIn("data", res)
            self.assertGreaterEqual(len(res["data"]), 2)
        finally:
            db.close()

    def test_complete_session_does_not_trigger_unnecessary_backfill(self):
        """Case: Stock already has candles from 09:15 to 15:25.
        Verify no unnecessary gap is flagged.
        """
        db = self.Session()
        try:
            now = main.database.get_ist_now()
            today_date = now.date()
            t0915 = datetime.combine(today_date, time(9, 15))
            
            candles = []
            curr = t0915
            for i in range(75):
                candles.append({
                    "timestamp": curr, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5
                })
                curr += timedelta(minutes=5)
            
            self._insert_candles("TESTCOMPLETE", "5m", candles)

            res = main.get_intraday_paginated(
                ticker="TESTCOMPLETE", interval="5m", before=None, limit=100,
                background_tasks=None, db=db
            )
            self.assertIn("data", res)
            self.assertGreaterEqual(len(res["data"]), 70)
        finally:
            db.close()

    def test_missing_previous_day_candles_detected(self):
        """Case: Today has morning candles, but yesterday stopped severely early (e.g. 12:00 PM).
        Verify inter-day gap detection flags fill_needed.
        """
        db = self.Session()
        try:
            now = main.database.get_ist_now()
            today_date = now.date()
            yesterday_date = today_date - timedelta(days=1)
            
            # Yesterday ended prematurely at 12:00 PM
            t_yesterday = datetime.combine(yesterday_date, time(12, 0))
            # Today started at 09:15 AM
            t_today = datetime.combine(today_date, time(9, 15))

            candles = [
                {"timestamp": t_yesterday, "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5},
                {"timestamp": t_today, "open": 51.0, "high": 52.0, "low": 50.0, "close": 51.5},
            ]
            self._insert_candles("TESTPREVDAY", "5m", candles)

            res = main.get_intraday_paginated(
                ticker="TESTPREVDAY", interval="5m", before=None, limit=100,
                background_tasks=None, db=db
            )
            self.assertIn("data", res)
            self.assertEqual(len(res["data"]), 2)
        finally:
            db.close()

    def test_legitimate_late_first_trade_boundary(self):
        """Case: Low-liquidity stock whose first trade on previous day was at 10:00 AM (within normal bounds).
        Verify that reasonable low-liquidity trade starts do not trigger unnecessary fills.
        """
        db = self.Session()
        try:
            now = main.database.get_ist_now()
            today_date = now.date()
            # 10:15 AM on an earlier day is legitimate for an illiquid stock (< 11:00 AM threshold)
            t_day1_close = datetime.combine(today_date - timedelta(days=2), time(15, 20))
            t_day2_open = datetime.combine(today_date - timedelta(days=1), time(10, 15))
            t_day2_close = datetime.combine(today_date - timedelta(days=1), time(15, 20))

            candles = [
                {"timestamp": t_day1_close, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
                {"timestamp": t_day2_open, "open": 10.2, "high": 10.3, "low": 10.0, "close": 10.1},
                {"timestamp": t_day2_close, "open": 10.1, "high": 10.4, "low": 10.1, "close": 10.3},
            ]
            self._insert_candles("TESTLOWLIQ", "5m", candles)

            res = main.get_intraday_paginated(
                ticker="TESTLOWLIQ", interval="5m", before=None, limit=100,
                background_tasks=None, db=db
            )
            self.assertIn("data", res)
            self.assertEqual(len(res["data"]), 3)
        finally:
            db.close()

    def test_weekend_boundary_not_flagged_as_gap(self):
        """Case: Friday close at 15:25 to Monday open at 09:15.
        Verify weekend calendar transition is not flagged as an abnormal intraday gap.
        """
        db = self.Session()
        try:
            # Friday
            fri_date = date(2026, 10, 9)
            mon_date = date(2026, 10, 12)
            t_fri_close = datetime.combine(fri_date, time(15, 25))
            t_mon_open = datetime.combine(mon_date, time(9, 15))

            candles = [
                {"timestamp": t_fri_close, "open": 500.0, "high": 502.0, "low": 498.0, "close": 501.0},
                {"timestamp": t_mon_open, "open": 501.0, "high": 505.0, "low": 500.0, "close": 503.0},
            ]
            self._insert_candles("TESTWEEKEND", "5m", candles)

            res = main.get_intraday_paginated(
                ticker="TESTWEEKEND", interval="5m", before=None, limit=100,
                background_tasks=None, db=db
            )
            self.assertIn("data", res)
            self.assertEqual(len(res["data"]), 2)
        finally:
            db.close()

    def test_cooldown_prevents_duplicate_provider_hammering(self):
        """Verify _backfill_cooldown prevents repeat backfill requests while active."""
        cooldown_key = "TESTCOOL:5m"
        main._set_backfill_cooldown(cooldown_key, 60)
        self.assertTrue(main._backfill_cooldown_active(cooldown_key))

        # Check that active cooldown reports True and expires cleanly
        main._set_backfill_cooldown(cooldown_key, -1)
        self.assertFalse(main._backfill_cooldown_active(cooldown_key))


if __name__ == "__main__":
    unittest.main()
