import unittest
import time
from live_timeframe_manager import LiveTimeframeManager, LiveTfBuilder
from config.timeframe_registry import TIMEFRAME_REGISTRY


class TestLiveTfBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = LiveTfBuilder(ticker="RELIANCE", timeframe="15m")

    def test_initial_state(self):
        self.assertEqual(self.builder.ticker, "RELIANCE")
        self.assertEqual(self.builder.timeframe, "15m")
        self.assertEqual(self.builder.viewer_count, 0)
        self.assertIsNone(self.builder.forming_candle)
        self.assertEqual(self.builder.completed_candles, [])

    def test_update_from_5m_without_viewer(self):
        self.builder.update_from_5m({"time": 1000, "open": 100, "high": 105,
                                      "low": 95, "close": 102, "volume": 1000})
        self.assertIsNone(self.builder.forming_candle)

    def test_update_from_5m_with_viewer(self):
        self.builder.viewer_count = 1
        self.builder.update_from_5m({"time": 1000, "open": 100, "high": 105,
                                      "low": 95, "close": 102, "volume": 1000})
        self.assertIsNotNone(self.builder.forming_candle)
        self.assertEqual(self.builder.forming_candle["open"], 100)

    def test_update_from_5m_extends_high(self):
        self.builder.viewer_count = 1
        self.builder.update_from_5m({"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000})
        self.builder.update_from_5m({"time": 1001, "open": 102, "high": 110, "low": 101, "close": 108, "volume": 500})
        self.assertEqual(self.builder.forming_candle["high"], 110)
        self.assertEqual(self.builder.forming_candle["volume"], 1500)

    def test_push_completed_5m_creates_first(self):
        self.builder.push_completed_5m({"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000})
        self.assertIsNotNone(self.builder.forming_candle)

    def test_push_completed_5m_moves_to_completed(self):
        self.builder.push_completed_5m({"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000})
        self.builder.push_completed_5m({"time": 1001, "open": 102, "high": 108, "low": 100, "close": 105, "volume": 800})
        self.assertEqual(len(self.builder.completed_candles), 1)

    def test_initialize_from_candles(self):
        candles = [
            {"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
            {"time": 1001, "open": 102, "high": 108, "low": 100, "close": 105, "volume": 800},
        ]
        self.builder.initialize_from_candles(candles)
        self.assertIsNotNone(self.builder.forming_candle)
        self.assertEqual(len(self.builder.completed_candles), 1)
        self.assertEqual(self.builder.completed_candles[0]["close"], 102)

    def test_get_snapshot(self):
        self.builder.viewer_count = 1
        self.builder.update_from_5m({"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000})
        snap = self.builder.get_snapshot()
        self.assertEqual(snap["viewer_count"], 1)
        self.assertIsNotNone(snap["forming"])
        self.assertEqual(len(snap["completed"]), 0)

    def test_completed_candles_not_exceed_200(self):
        for i in range(250):
            self.builder.push_completed_5m({"time": i, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000})
        self.assertLessEqual(len(self.builder.completed_candles), 200)

    def test_get_snapshot_returns_last_50(self):
        for i in range(100):
            self.builder.push_completed_5m({"time": i, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000})
        snap = self.builder.get_snapshot()
        self.assertLessEqual(len(snap["completed"]), 50)


class TestLiveTimeframeManager(unittest.TestCase):
    def setUp(self):
        self.mgr = LiveTimeframeManager()
        self.mgr._cleanup_thread_running = False

    def test_add_viewer_creates_builder(self):
        self.mgr.add_viewer("RELIANCE", "15m")
        self.assertTrue(self.mgr.builder_exists("RELIANCE", "15m"))

    def test_add_viewer_increments_count(self):
        self.mgr.add_viewer("RELIANCE", "15m")
        self.mgr.add_viewer("RELIANCE", "15m")
        snap = self.mgr.get_current("RELIANCE", "15m")
        self.assertEqual(snap["viewer_count"], 2)

    def test_remove_viewer_decrements_count(self):
        self.mgr.add_viewer("RELIANCE", "15m")
        self.mgr.add_viewer("RELIANCE", "15m")
        self.mgr.remove_viewer("RELIANCE", "15m")
        snap = self.mgr.get_current("RELIANCE", "15m")
        self.assertEqual(snap["viewer_count"], 1)

    def test_remove_viewer_no_negative(self):
        self.mgr.remove_viewer("RELIANCE", "15m")
        snap = self.mgr.get_current("RELIANCE", "15m")
        self.assertIsNone(snap)

    def test_add_viewer_unknown_tf(self):
        self.mgr.add_viewer("RELIANCE", "999m")
        self.assertFalse(self.mgr.builder_exists("RELIANCE", "999m"))

    def test_add_viewer_multiple_tfs(self):
        self.mgr.add_viewer("RELIANCE", "15m")
        self.mgr.add_viewer("RELIANCE", "1h")
        self.assertTrue(self.mgr.builder_exists("RELIANCE", "15m"))
        self.assertTrue(self.mgr.builder_exists("RELIANCE", "1h"))

    def test_get_current_no_builder(self):
        snap = self.mgr.get_current("NONEXISTENT", "15m")
        self.assertIsNone(snap)

    def test_get_all_active_empty(self):
        active = self.mgr.get_all_active()
        self.assertEqual(active, {})

    def test_get_all_active_with_viewers(self):
        self.mgr.add_viewer("R1", "15m")
        self.mgr.add_viewer("R2", "1h")
        active = self.mgr.get_all_active()
        self.assertIn("R1", active)
        self.assertIn("R2", active)

    def test_builder_exists_no(self):
        self.assertFalse(self.mgr.builder_exists("R1", "15m"))

    def test_get_stats(self):
        stats = self.mgr.get_stats()
        self.assertEqual(stats["total_builders"], 0)
        self.assertEqual(stats["max_builders"], 5000)

    def test_get_stats_after_add(self):
        self.mgr.add_viewer("R1", "15m")
        stats = self.mgr.get_stats()
        self.assertEqual(stats["total_builders"], 1)
        self.assertEqual(stats["total_viewers"], 1)
        self.assertEqual(stats["active_builders"], 1)

    def test__evict_lru_removes_idle(self):
        self.mgr.add_viewer("R1", "15m")
        self.mgr.add_viewer("R2", "1h")
        self.mgr.remove_viewer("R1", "15m")  # viewer_count=0
        self.mgr._evict_lru()
        self.assertFalse(self.mgr.builder_exists("R1", "15m"))
        self.assertTrue(self.mgr.builder_exists("R2", "1h"))


if __name__ == "__main__":
    unittest.main()
