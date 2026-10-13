"""5-MINUTE SOURCE-OF-TRUTH architecture tests.

Rules under test:
  * 15m/30m/1h are DERIVED from stored 5m, not independently downloaded.
  * Resampled buckets align to the 09:15 NSE session open and aggregate OHLCV
    correctly (open=first, high=max, low=min, close=last, volume=sum).
  * Weekends and exchange holidays are NOT missing data.
  * The background 5m sweep yields during market hours and only serves
    user-opened (queued) tickers then.
"""
import datetime
import inspect
import os
import unittest
from unittest import mock
from datetime import datetime as dt, timedelta

from resampler import CandleResampler
import main

SESSION_OPEN = dt(2026, 10, 13, 9, 15)


def _bars(n, start=SESSION_OPEN):
    out = []
    for i in range(n):
        p = 100.0 + i
        out.append({"timestamp": start + timedelta(minutes=5 * i),
                    "open": p, "high": p + 2, "low": p - 1, "close": p + 1,
                    "volume": 10 + i})
    return out


class ResampleFrom5mTests(unittest.TestCase):
    def test_15m_buckets_align_to_0915_and_aggregate_ohlcv(self):
        out = CandleResampler.resample_5m_to(_bars(6), "15m")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["timestamp"].strftime("%H:%M"), "09:15")
        self.assertEqual(out[1]["timestamp"].strftime("%H:%M"), "09:30")
        b = out[0]
        self.assertEqual(b["open"], 100.0)            # first 5m open
        self.assertEqual(b["high"], max(x["high"] for x in _bars(6)[:3]))
        self.assertEqual(b["low"], min(x["low"] for x in _bars(6)[:3]))
        self.assertEqual(b["close"], _bars(6)[2]["close"])   # last 5m close
        self.assertEqual(b["volume"], sum(x["volume"] for x in _bars(6)[:3]))

    def test_1h_bucket_starts_at_0915(self):
        out = CandleResampler.resample_5m_to(_bars(24), "1h")
        self.assertEqual(out[0]["timestamp"].strftime("%H:%M"), "09:15")

    def test_resampled_candles_never_exceed_the_source_count(self):
        src = _bars(6)
        for tf in ("15m", "30m", "1h"):
            out = CandleResampler.resample_5m_to(src, tf)
            self.assertLessEqual(len(out), len(src))
            self.assertTrue(all("open" in c and "volume" in c for c in out))

    def test_no_source_means_no_candles(self):
        # An incomplete aggregate must never be invented.
        self.assertEqual(CandleResampler.resample_5m_to([], "15m"), [])


class TradingDayTests(unittest.TestCase):
    def test_weekends_are_not_trading_days(self):
        # Fri 9 Oct -> Mon 12 Oct 2026 (Sat/Sun excluded)
        days = main._trading_days_between(dt(2026, 10, 9), dt(2026, 10, 12))
        self.assertIn(datetime.date(2026, 10, 9), days)
        self.assertIn(datetime.date(2026, 10, 12), days)
        self.assertNotIn(datetime.date(2026, 10, 10), days)   # Saturday
        self.assertNotIn(datetime.date(2026, 10, 11), days)   # Sunday

    def test_holidays_are_not_trading_days(self):
        with mock.patch.dict(main.NSE_HOLIDAYS, {datetime.date(2026, 10, 13): "test"}, clear=False):
            days = main._trading_days_between(dt(2026, 10, 13), dt(2026, 10, 13))
        self.assertEqual(days, set())


class ArchitectureTests(unittest.TestCase):
    """The structural guarantees, asserted against the real source."""

    def setUp(self):
        self.src = inspect.getsource(main)

    def test_higher_timeframes_are_resampled_before_any_provider_call(self):
        i_resample = self.src.find("5-MINUTE IS THE SOURCE OF TRUTH")
        i_provider = self.src.find("angel_interval_map = {")
        self.assertNotEqual(i_resample, -1, "resample-first block missing")
        self.assertNotEqual(i_provider, -1)
        self.assertLess(i_resample, i_provider,
                        "resampling must be attempted BEFORE the provider fetch")
        block = self.src[i_resample:i_provider]
        self.assertIn("resample_5m_to", block)
        self.assertIn("_trading_days_between", block)
        self.assertIn("issubset", block,
                      "higher timeframes may only be derived when the 5m source "
                      "covers every trading session in the range")

    def test_market_hours_yield_to_user_traffic(self):
        i = self.src.find("async def _intraday_gap_sweep_loop")
        self.assertNotEqual(i, -1)
        block = self.src[i:i + 1600]
        self.assertIn("market_open = is_market_open_now()", block)
        self.assertIn("elif not market_open and tick % 15 == 0", block,
                      "the rotating background sweep must not run during market hours")
        self.assertIn("if priority:", block,
                      "user-opened tickers still get served during market hours")

    def test_sweep_enqueues_exact_range_jobs(self):
        i = self.src.find("def _sweep_intraday_gaps_once")
        block = self.src[i:i + 3600]
        self.assertIn("_bj.enqueue(t, '5m', _js, _je", block,
                      "the sweep must schedule an exact-range repair job")
        self.assertIn("_stable_job_range('5m', now)", block,
                      "the sweep must use session-stable bounds, not a raw now")
        self.assertNotIn("perform_on_demand_backfill(t, '5m'", block,
                         "the sweep must not fetch inline; the leased worker owns fetching")


if __name__ == "__main__":
    unittest.main()
