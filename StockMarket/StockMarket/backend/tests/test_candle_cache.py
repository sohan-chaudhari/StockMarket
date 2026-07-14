import unittest
import time
from datetime import datetime
from unittest.mock import MagicMock
from candle_cache import CandleCache, CacheEntry


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


if __name__ == "__main__":
    unittest.main()
