import unittest
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from aggregator import Live5mBuilder, PendingCandleManager, snap_to_nse_session, validate_ohlc, fix_ohlc, is_trading_day, is_market_hour


class TestUtils(unittest.TestCase):
    def test_snap_to_nse_session_market_hours(self):
        # 10:00 AM IST = 04:30 UTC
        from datetime import timezone
        utc_ts = int(datetime(2026, 7, 1, 4, 30, 0, tzinfo=timezone.utc).timestamp())
        snapped = snap_to_nse_session(utc_ts, 5)
        snapped_dt = datetime.fromtimestamp(snapped, tz=timezone.utc)
        # snapped should be 10:00 IST = 04:30 UTC
        self.assertEqual(snapped_dt.hour, 4)
        self.assertEqual(snapped_dt.minute, 30)

    def test_snap_to_nse_session_pre_market(self):
        dt = datetime(2026, 7, 1, 3, 0, 0)
        epoch = int(dt.replace(tzinfo=None).timestamp()) + 19800
        snapped = snap_to_nse_session(epoch, 5)
        snapped_dt = datetime.fromtimestamp(snapped, tz=None)
        self.assertEqual(snapped_dt.hour, 9)
        self.assertEqual(snapped_dt.minute, 15)

    def test_snap_to_nse_session_post_market(self):
        dt = datetime(2026, 7, 1, 16, 0, 0)
        epoch = int(dt.replace(tzinfo=None).timestamp()) + 19800
        snapped = snap_to_nse_session(epoch, 5)
        snapped_dt = datetime.fromtimestamp(snapped, tz=None)
        # Should snap to last 5m bucket before 15:30
        self.assertEqual(snapped_dt.hour, 15)
        self.assertEqual(snapped_dt.minute, 25)

    def test_snap_to_nse_session_grace_window_no_phantom(self):
        # Ticks at exactly 15:30:00 and inside the grace window (15:30-15:44:59)
        # must snap to 15:25 (the last real session bucket), NOT create a
        # phantom 15:30 bucket outside the session.
        for minute_offset in (0, 5, 14, 14 * 60 + 59):
            dt = datetime(2026, 7, 1, 15, 30, 0)
            epoch = int(dt.replace(tzinfo=None).timestamp()) + 19800 + minute_offset
            snapped = snap_to_nse_session(epoch, 5)
            snapped_dt = datetime.fromtimestamp(snapped, tz=None)
            self.assertEqual(snapped_dt.hour, 15, msg=f"offset={minute_offset}")
            self.assertEqual(snapped_dt.minute, 25, msg=f"offset={minute_offset}")

    def test_validate_ohlc_valid(self):
        valid, _ = validate_ohlc(100, 105, 95, 102)
        self.assertTrue(valid)

    def test_validate_ohlc_high_less_than_open(self):
        valid, msg = validate_ohlc(100, 99, 95, 102)
        self.assertFalse(valid)

    def test_validate_ohlc_low_greater_than_open(self):
        valid, msg = validate_ohlc(100, 105, 101, 102)
        self.assertFalse(valid)

    def test_fix_ohlc(self):
        o, h, l, c = fix_ohlc(100, 90, 110, 95)
        self.assertEqual(h, 100)  # fixed high = max(90, 100, 95) = 100
        self.assertEqual(l, 95)   # fixed low = min(110, 100, 95) = 95

    def test_is_trading_day_weekend(self):
        sat = datetime(2026, 7, 4).date()
        self.assertFalse(is_trading_day(sat))

    def test_is_trading_day_with_holiday(self):
        d = datetime(2026, 7, 1).date()
        self.assertFalse(is_trading_day(d, {d}))

    # ── Decision 4: special/shortened session overrides ─────────────────────

    def test_is_market_hour_rejects_standard_hours_by_default(self):
        # 18:15 IST is well outside the standard 09:15-15:45(grace) window.
        evening = datetime(2026, 7, 1, 18, 15, 0)
        self.assertFalse(is_market_hour(evening))

    def test_is_market_hour_accepts_special_session_override(self):
        # Same 18:15 IST instant, but with a Muhurat-style 18:15-19:15 window
        # passed explicitly — this is exactly what Live5mBuilder._session_bounds
        # produces for a date registered via set_special_sessions().
        evening = datetime(2026, 7, 1, 18, 15, 0)
        special_open = 18 * 3600 + 15 * 60
        special_close_grace = 19 * 3600 + 15 * 60 + 15 * 60
        self.assertTrue(is_market_hour(evening, special_open, special_close_grace))

    def test_snap_to_nse_session_default_unaffected_by_new_params(self):
        # Omitting the new optional args must reproduce the exact standard-day
        # bucket the pre-existing tests assert — i.e. zero behavior change for
        # every normal trading day.
        from datetime import timezone
        utc_ts = int(datetime(2026, 7, 1, 4, 30, 0, tzinfo=timezone.utc).timestamp())
        snapped = snap_to_nse_session(utc_ts, 5)
        snapped_dt = datetime.fromtimestamp(snapped, tz=timezone.utc)
        self.assertEqual(snapped_dt.hour, 4)
        self.assertEqual(snapped_dt.minute, 30)

    def test_snap_to_nse_session_special_window(self):
        # A tick at 18:20 IST during a registered 18:15-19:15 special session
        # should snap to the 18:15 bucket (the session's own open), not fall
        # through the standard-day "post-close" branch.
        from datetime import timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        tick = datetime(2026, 10, 21, 18, 20, 0, tzinfo=ist)
        ts_epoch = int(tick.timestamp())
        special_open = 18 * 3600 + 15 * 60
        special_close = 19 * 3600 + 15 * 60
        special_close_grace = special_close + 15 * 60
        snapped = snap_to_nse_session(ts_epoch, 5, special_open, special_close, special_close_grace)
        snapped_dt = datetime.fromtimestamp(snapped, tz=ist)
        self.assertEqual(snapped_dt.hour, 18)
        self.assertEqual(snapped_dt.minute, 20)


class TestSpecialSessionWiring(unittest.TestCase):
    """Live5mBuilder honors a registered special session end-to-end via process_tick.

    process_tick substitutes the real server clock for tick_ts whenever the
    supplied timestamp looks "frozen" (>30s behind the actual wall clock) —
    a deliberate guard against a stale broker feed. That means a fixed
    Muhurat-style timestamp can't be asserted against reliably unless
    aggregator's own clock is patched to match it, exactly like the existing
    process_tick tests above do for is_market_hour/is_trading_day.
    """

    def setUp(self):
        self.builder = Live5mBuilder()

    def tearDown(self):
        self.builder._flush_worker_running = False

    def test_tick_outside_standard_hours_rejected_without_special_session(self):
        from datetime import timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        tick_dt = datetime(2026, 10, 21, 18, 20, 0, tzinfo=ist)
        ts = int(tick_dt.timestamp())
        naive_now = tick_dt.replace(tzinfo=None)
        with patch("aggregator.ist_now_naive", return_value=naive_now), \
             patch("time.time", return_value=ts):
            result = self.builder.process_tick("MUHURAT", 100.0, volume=10, tick_ts=ts)
        self.assertEqual(result, {})

    def test_tick_during_registered_special_session_builds_a_candle(self):
        from datetime import date as date_cls, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        d = date_cls(2026, 10, 21)
        special_open = 18 * 3600 + 15 * 60
        special_close = 19 * 3600 + 15 * 60
        self.builder.set_special_sessions({d: (special_open, special_close, special_close + 15 * 60)})

        tick_dt = datetime(2026, 10, 21, 18, 20, 0, tzinfo=ist)
        ts = int(tick_dt.timestamp())
        naive_now = tick_dt.replace(tzinfo=None)
        with patch("aggregator.ist_now_naive", return_value=naive_now), \
             patch("time.time", return_value=ts):
            result = self.builder.process_tick("MUHURAT", 100.0, volume=10, tick_ts=ts)
        self.assertIn("5m", result)
        self.assertEqual(result["5m"]["close"], 100.0)

    def test_session_bounds_defaults_to_standard_when_no_override(self):
        from datetime import date as date_cls
        bounds = self.builder._session_bounds(date_cls(2026, 7, 1))
        self.assertEqual(bounds, (9 * 3600 + 15 * 60, 15 * 3600 + 30 * 60, 15 * 3600 + 45 * 60))

    def test_is_trading_day_normal(self):
        wed = datetime(2026, 7, 1).date()
        if wed.weekday() < 5:
            self.assertTrue(is_trading_day(wed))
        else:
            self.assertTrue(is_trading_day(wed, set()))

    def test_is_market_hour_open(self):
        dt = datetime(2026, 7, 1, 10, 0, 0)
        self.assertTrue(is_market_hour(dt))

    def test_is_market_hour_closed(self):
        dt = datetime(2026, 7, 1, 8, 0, 0)
        self.assertFalse(is_market_hour(dt))


class TestPendingCandleManager(unittest.TestCase):
    def setUp(self):
        self.mgr = PendingCandleManager(late_buffer_sec=60)
        self.candle = {
            "timestamp": datetime(2026, 7, 1, 10, 0, 0),
            "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000,
        }

    def test_add_or_update_new(self):
        pending = self.mgr.add_or_update("RELIANCE", self.candle, time.time())
        self.assertEqual(pending.ticker, "RELIANCE")
        self.assertEqual(pending.open, 100)
        self.assertEqual(pending.high, 105)
        self.assertEqual(pending.volume, 1000)

    def test_add_or_update_updates_existing(self):
        self.mgr.add_or_update("RELIANCE", self.candle, time.time())
        update = dict(self.candle, high=110, close=108, volume=500)
        pending = self.mgr.add_or_update("RELIANCE", update, time.time())
        self.assertEqual(pending.high, 110)
        self.assertEqual(pending.close, 108)
        self.assertEqual(pending.volume, 1500)

    def test_get_expired_none(self):
        expired = self.mgr.get_expired(time.time())
        self.assertEqual(len(expired), 0)

    def test_get_expired_with_old(self):
        self.mgr.add_or_update("RELIANCE", self.candle, time.time() - 120)
        expired = self.mgr.get_expired(time.time())
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].ticker, "RELIANCE")
        self.assertTrue(expired[0].finalized)

    def test_remove(self):
        self.mgr.add_or_update("RELIANCE", self.candle, time.time())
        self.mgr.remove("RELIANCE")
        self.assertEqual(self.mgr.count(), 0)

    def test_clear_all(self):
        self.mgr.add_or_update("R1", self.candle, time.time())
        self.mgr.add_or_update("R2", self.candle, time.time())
        self.mgr.clear_all()
        self.assertEqual(self.mgr.count(), 0)


class TestLive5mBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = Live5mBuilder()
        self.builder.set_holidays(set())

    def tearDown(self):
        self.builder._flush_worker_running = False
        if self.builder._flush_thread.is_alive():
            self.builder._flush_thread.join(timeout=3)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_process_tick_creates_candle(self, mock_td, mock_mh):
        res = self.builder.process_tick("RELIANCE", 2500.0, volume=100,
                                         tick_ts=datetime(2026, 7, 1, 10, 0, 0).timestamp() + 19800)
        self.assertIn("5m", res)
        self.assertEqual(res["5m"]["open"], 2500.0)
        self.assertEqual(res["5m"]["high"], 2500.0)
        self.assertEqual(res["5m"]["close"], 2500.0)
        self.assertEqual(res["5m"]["volume"], 100)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_process_tick_updates_ohlc(self, mock_td, mock_mh):
        ts = datetime(2026, 7, 1, 10, 0, 0).timestamp() + 19800
        self.builder.process_tick("RELIANCE", 2500.0, volume=100, tick_ts=ts)
        res = self.builder.process_tick("RELIANCE", 2550.0, volume=50, tick_ts=ts + 60)
        self.assertEqual(res["5m"]["high"], 2550.0)
        self.assertEqual(res["5m"]["close"], 2550.0)
        self.assertEqual(res["5m"]["volume"], 150)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_get_current_returns_snapshot(self, mock_td, mock_mh):
        ts = datetime(2026, 7, 1, 10, 0, 0).timestamp() + 19800
        self.builder.process_tick("RELIANCE", 2500.0, tick_ts=ts)
        snap = self.builder.get_current("RELIANCE")
        self.assertIsNotNone(snap)
        self.assertIn("5m", snap)
        self.assertEqual(snap["5m"]["open"], 2500.0)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_get_current_nonexistent(self, mock_td, mock_mh):
        snap = self.builder.get_current("NONEXISTENT")
        self.assertIsNone(snap)

    @patch("aggregator.is_market_hour", return_value=False)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_post_market_flush(self, mock_td, mock_mh):
        self.builder._flush_batch_now = MagicMock()
        mock_now = datetime(2026, 7, 1, 16, 0, 0)
        ts = mock_now.timestamp() + 19800
        self.builder.active_candles["RELIANCE"] = {
            "5m": {"timestamp": datetime(2026, 7, 1, 15, 25, 0),
                    "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        }
        with patch("aggregator.ist_now_naive", return_value=mock_now), \
             patch("time.time", return_value=ts):
            res = self.builder.process_tick("RELIANCE", 102.0, tick_ts=ts)
        self.assertEqual(res, {})
        self.assertNotIn("RELIANCE", self.builder.active_candles)

    def test_stale_tick_skipped(self):
        ts = time.time()
        self.builder._last_tick_ts["RELIANCE"] = ts + 120
        with patch("aggregator.is_market_hour", return_value=True), \
             patch("aggregator.is_trading_day", return_value=True):
            res = self.builder.process_tick("RELIANCE", 2500.0, tick_ts=ts)
        self.assertEqual(res, {})

    def test_init_ticker_from_last_candle(self):
        last = {"close": 2450.0}
        self.builder.init_ticker_from_last_candle("RELIANCE", last)
        self.assertIn("RELIANCE", self.builder.active_candles)
        self.assertEqual(self.builder._previous_closes["RELIANCE"], 2450.0)

    def test_set_previous_close(self):
        self.builder.set_previous_close("RELIANCE", 2400.0)
        self.assertEqual(self.builder._previous_closes["RELIANCE"], 2400.0)

    def test_get_stats(self):
        stats = self.builder.get_stats()
        self.assertIn("tickers", stats)
        self.assertIn("stale_skips", stats)
        self.assertIn("pending_flush", stats)
        self.assertNotIn("backpressure_events", stats)

    def test_fix_ohlc_ensures_valid(self):
        with patch("aggregator.is_market_hour", return_value=True), \
             patch("aggregator.is_trading_day", return_value=True):
            ts = datetime(2026, 7, 1, 10, 0, 0).timestamp() + 19800
            self.builder.process_tick("RELIANCE", 100.0, tick_ts=ts)
            # Simulate tick that violates OHLC
            self.builder.active_candles["RELIANCE"]["5m"]["high"] = 90
            snap = self.builder.get_current("RELIANCE")
            self.assertGreaterEqual(snap["5m"]["high"], snap["5m"]["open"])


if __name__ == "__main__":
    unittest.main()
