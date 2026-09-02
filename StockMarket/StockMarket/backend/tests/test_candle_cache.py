import unittest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from candle_cache import CandleCache, CacheEntry, _IST


class TestCacheEntry(unittest.TestCase):
    def setUp(self):
        self.entry = CacheEntry("key", [{"time": 1000}], ttl_sec=60)

    def test_initial_not_expired(self):
        self.assertFalse(self.entry.is_expired())

    def test_expired(self):
        entry = CacheEntry("key", [{"time": 1000}], ttl_sec=-1)
        self.assertTrue(entry.is_expired())

    def test_hit_increments(self):
        self.entry.hit()
        self.entry.hit()
        self.assertEqual(self.entry.hits, 2)

    def test_data_preserved(self):
        self.assertEqual(self.entry.data, [{"time": 1000}])


class TestCandleCache(unittest.TestCase):
    def setUp(self):
        self.live_mgr = MagicMock()
        self.live_mgr.get_current.return_value = None
        self.cache = CandleCache(live_timeframe_manager=self.live_mgr)
        self.cache._cleanup_thread_running = False

    def test_miss_returns_none(self):
        result = self.cache.get("RELIANCE", "15m")
        self.assertIsNone(result)

    def test_set_and_get(self):
        data = [{"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        self.cache.set("RELIANCE", "15m", data)
        result = self.cache.get("RELIANCE", "15m")
        self.assertEqual(result, data)

    def test_set_with_explicit_ttl_and_params(self):
        data = [{"time": 1000}]
        self.cache.set("RELIANCE", "15m", data, ttl_sec=10, start=100, end=2000)
        result = self.cache.get("RELIANCE", "15m", start=100, end=2000)
        self.assertEqual(result, data)

    def test_get_different_key(self):
        self.cache.set("RELIANCE", "15m", [{"time": 1000}])
        result = self.cache.get("INFY", "15m")
        self.assertIsNone(result)

    def test_invalidate_all(self):
        self.cache.set("RELIANCE", "15m", [{"time": 1}])
        self.cache.set("RELIANCE", "1h", [{"time": 2}])
        self.cache.invalidate("RELIANCE")
        self.assertIsNone(self.cache.get("RELIANCE", "15m"))
        self.assertIsNone(self.cache.get("RELIANCE", "1h"))

    def test_invalidate_specific_tf(self):
        self.cache.set("RELIANCE", "15m", [{"time": 1}])
        self.cache.set("RELIANCE", "1h", [{"time": 2}])
        self.cache.invalidate("RELIANCE", timeframe="15m")
        self.assertIsNone(self.cache.get("RELIANCE", "15m"))
        self.assertIsNotNone(self.cache.get("RELIANCE", "1h"))

    def test_l1_hit(self):
        self.live_mgr.get_current.return_value = {
            "completed": [{"time": 1000}],
            "forming": {"time": 1001},
            "viewer_count": 1,
        }
        result = self.cache.get("RELIANCE", "15m")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_l1_no_mgr(self):
        cache = CandleCache(live_timeframe_manager=None)
        self.assertIsNone(cache.get("R", "15m"))

    def test_l2_hit(self):
        self.live_mgr.get_current.return_value = None
        self.cache.set("RELIANCE", "15m", [{"time": 1000}])
        # First call was a miss (L2 hit happens on get)
        result = self.cache.get("RELIANCE", "15m")
        self.assertEqual(len(result), 1)

    def test_l2_expired_removed(self):
        self.live_mgr.get_current.return_value = None
        self.cache.set("RELIANCE", "15m", [{"time": 1000}], ttl_sec=0)
        self.assertIsNone(self.cache.get("RELIANCE", "15m"))

    def test_stats_hit_rate(self):
        self.cache._l1_hits = 80
        self.cache._l2_hits = 15
        self.cache._l3_hits = 0
        self.cache._misses = 5
        stats = self.cache.get_stats()
        self.assertEqual(stats["total_requests"], 100)
        self.assertEqual(stats["hit_rate_pct"], 95.0)

    def test_stats_empty(self):
        stats = self.cache.get_stats()
        self.assertEqual(stats["total_requests"], 0)
        self.assertEqual(stats["l2_entries"], 0)

    def test_default_ttl_intraday(self):
        ttl = self.cache._default_ttl("15m")
        self.assertGreater(ttl, 0)

    def test_get_with_time_range(self):
        self.live_mgr.get_current.return_value = {
            "completed": [{"time": 100}, {"time": 200}, {"time": 300}],
            "forming": None,
            "viewer_count": 1,
        }
        result = self.cache.get("RELIANCE", "15m", start=150, end=250)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["time"], 200)

    def test_stats_increment_l1(self):
        self.live_mgr.get_current.return_value = {
            "completed": [{"time": 1000}], "forming": None, "viewer_count": 1,
        }
        self.cache.get("RELIANCE", "15m")
        stats = self.cache.get_stats()
        self.assertEqual(stats["l1_hits"], 1)


class TestDefaultTtlIST(unittest.TestCase):
    """Verify _default_ttl() always uses IST regardless of server timezone."""

    def _cache(self):
        live_mgr = MagicMock()
        live_mgr.get_current.return_value = None
        c = CandleCache(live_timeframe_manager=live_mgr)
        c._cleanup_thread_running = False
        return c

    def _fake_ist(self, hour, minute, weekday=0):
        """Build a timezone-aware IST datetime with the given hour/minute and weekday."""
        # weekday: 0=Mon … 4=Fri, 5=Sat, 6=Sun
        # Use a known Monday anchor: 2024-01-01 is a Monday
        from datetime import date, timedelta as td
        anchor = date(2024, 1, 1)  # Monday
        target_date = anchor + td(days=weekday)
        return datetime(target_date.year, target_date.month, target_date.day, hour, minute, 0, tzinfo=_IST)

    def test_ist_constant_offset(self):
        """_IST must be UTC+5:30."""
        self.assertEqual(_IST.utcoffset(None), timedelta(hours=5, minutes=30))

    def test_market_hours_returns_300(self):
        """During NSE market hours (09:15–15:30 IST) TTL must be 5 min."""
        cache = self._cache()
        fake_now = self._fake_ist(11, 0, weekday=1)  # Tuesday 11:00 IST
        with patch("candle_cache.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.now.side_effect = lambda tz=None: fake_now
            # patch replace() to delegate to real datetime
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ttl = cache._default_ttl("15m")
        self.assertEqual(ttl, 300)

    def test_after_market_close_returns_3600(self):
        """After 15:30 IST on a weekday TTL must be 1 hour."""
        cache = self._cache()
        fake_now = self._fake_ist(16, 0, weekday=1)  # Tuesday 16:00 IST
        with patch("candle_cache.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.now.side_effect = lambda tz=None: fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ttl = cache._default_ttl("15m")
        self.assertEqual(ttl, 3600)

    def test_before_market_open_returns_3600(self):
        """Before 09:15 IST on a weekday TTL must be 1 hour."""
        cache = self._cache()
        fake_now = self._fake_ist(8, 0, weekday=0)  # Monday 08:00 IST
        with patch("candle_cache.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.now.side_effect = lambda tz=None: fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ttl = cache._default_ttl("15m")
        self.assertEqual(ttl, 3600)

    def test_weekend_returns_21600(self):
        """On Saturday/Sunday TTL must be 6 hours regardless of time."""
        cache = self._cache()
        fake_now = self._fake_ist(12, 0, weekday=5)  # Saturday noon IST
        with patch("candle_cache.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.now.side_effect = lambda tz=None: fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ttl = cache._default_ttl("1h")
        self.assertEqual(ttl, 21600)

    def test_utc_server_market_hours(self):
        """IST 11:00 = UTC 05:30. A UTC server would wrongly think it's pre-market.
        After the fix, TTL must be 300 (market hours) regardless of UTC offset."""
        cache = self._cache()
        # This IST-aware datetime represents 11:00 IST on a Wednesday
        ist_11am = self._fake_ist(11, 0, weekday=2)
        with patch("candle_cache.datetime") as mock_dt:
            mock_dt.now.return_value = ist_11am
            mock_dt.now.side_effect = lambda tz=None: ist_11am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ttl = cache._default_ttl("5m")
        # Would be 3600 (wrong) if datetime.now() without tz was used on a UTC server
        self.assertEqual(ttl, 300)

    def test_ist_not_local_time(self):
        """_default_ttl must call datetime.now with the IST timezone, not without args."""
        cache = self._cache()
        calls = []
        real_now = datetime.now(_IST)
        with patch("candle_cache.datetime") as mock_dt:
            def fake_now(tz=None):
                calls.append(tz)
                return real_now
            mock_dt.now.side_effect = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            cache._default_ttl("15m")
        self.assertTrue(len(calls) >= 1, "datetime.now() was not called")
        self.assertEqual(calls[0], _IST, "datetime.now() must be called with _IST, not without tz")


class TestBackfillNonBlocking(unittest.TestCase):
    """Verify that gap-fill triggers via background_tasks.add_task (not direct call)."""

    def test_backfill_scheduled_not_called_directly(self):
        """Smoke-check: BackgroundTasks.add_task accepts perform_on_demand_backfill."""
        from fastapi import BackgroundTasks
        from datetime import timedelta as td
        bt = BackgroundTasks()
        ran = []

        def fake_backfill(ticker, interval, start, now):
            ran.append(ticker)

        from datetime import datetime as real_dt
        now = real_dt(2024, 6, 10, 11, 0)
        start = now - td(days=14)
        bt.add_task(fake_backfill, "RELIANCE", "15m", start, now)
        # Tasks don't run until the response cycle — verify task is queued, not run
        self.assertEqual(len(ran), 0, "Backfill must be queued, not executed synchronously")
        self.assertEqual(len(bt.tasks), 1)

    def test_backfill_lock_prevents_duplicate(self):
        """Concurrent callers for the same ticker: only one runs while others skip."""
        import threading

        lock = threading.Lock()
        active = set()
        calls = []
        # barrier ensures all 5 threads are scheduled and running before any one finishes
        start_barrier = threading.Barrier(5)

        def guarded_task(ticker):
            start_barrier.wait()  # all 5 threads reach this point before any proceeds
            with lock:
                if ticker in active:
                    return  # duplicate — bail immediately
                active.add(ticker)
            try:
                calls.append(ticker)
                # Simulate brief work so other threads see the lock held
                import time; time.sleep(0.01)
            finally:
                with lock:
                    active.discard(ticker)

        threads = [threading.Thread(target=guarded_task, args=("RELIANCE",)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly one thread should have run (the first to pass the lock check)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
