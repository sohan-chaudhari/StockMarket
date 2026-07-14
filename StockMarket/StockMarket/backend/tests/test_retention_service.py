import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from retention_service import RetentionService, RetentionRule


class TestRetentionRule(unittest.TestCase):
    def test_rule_creation(self):
        rule = RetentionRule(source_tf="5m", target_tf="15m", after_days=120, batch_size=50)
        self.assertEqual(rule.source_tf, "5m")
        self.assertEqual(rule.target_tf, "15m")
        self.assertEqual(rule.after_days, 120)

    def test_rule_default_batch_size(self):
        rule = RetentionRule(source_tf="5m", target_tf="15m", after_days=120)
        self.assertEqual(rule.batch_size, 50)


class TestRetentionService(unittest.TestCase):
    def setUp(self):
        self.db_factory = MagicMock()
        self.resample_svc = MagicMock()
        self.service = RetentionService(
            db_session_factory=self.db_factory,
            resample_svc=self.resample_svc,
        )

    def test_load_policy(self):
        rules = self.service.load_policy()
        self.assertGreater(len(rules), 0)
        for rule in rules:
            self.assertIsInstance(rule, RetentionRule)
            self.assertIn(rule.source_tf, {"5m", "15m", "30m", "1h", "4h", "1D", "1W"})
            self.assertIn(rule.target_tf, {"15m", "30m", "1h", "4h", "1D", "1W", "1M"})

    def test_load_policy_order(self):
        rules = self.service.load_policy()
        for i in range(len(rules) - 1):
            self.assertGreaterEqual(rules[i + 1].after_days, rules[i].after_days)

    def test_checksum_empty(self):
        result = self.service._checksum([])
        self.assertEqual(result, {})

    def test_checksum_single_candle(self):
        candles = [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        cs = self.service._checksum(candles)
        self.assertEqual(cs["count"], 1)
        self.assertEqual(cs["open_first"], 100)
        self.assertEqual(cs["close_last"], 102)
        self.assertEqual(cs["high_max"], 105)
        self.assertEqual(cs["low_min"], 95)
        self.assertEqual(cs["volume_sum"], 1000)

    def test_checksum_multiple(self):
        candles = [
            {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
            {"open": 102, "high": 110, "low": 101, "close": 108, "volume": 500},
        ]
        cs = self.service._checksum(candles)
        self.assertEqual(cs["count"], 2)
        self.assertEqual(cs["open_first"], 100)
        self.assertEqual(cs["close_last"], 108)
        self.assertEqual(cs["high_max"], 110)
        self.assertEqual(cs["low_min"], 95)
        self.assertEqual(cs["volume_sum"], 1500)

    def test_verify_checksum_match(self):
        src = {"volume_sum": 1000, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        tgt = {"volume_sum": 1000, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        self.assertTrue(self.service._verify_checksum(src, tgt))

    def test_verify_checksum_mismatch(self):
        src = {"volume_sum": 1000, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        tgt = {"volume_sum": 900, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        self.assertFalse(self.service._verify_checksum(src, tgt))

    def test_verify_checksum_empty(self):
        self.assertFalse(self.service._verify_checksum({}, {"volume_sum": 0}))
        self.assertFalse(self.service._verify_checksum({"volume_sum": 0}, {}))

    def test_run_cycle_already_running(self):
        self.service._running = True
        self.service.run_cycle()
        # Should not crash, just print and return

    def test_run_cycle_skips_live_window(self):
        self.service.load_policy = MagicMock(return_value=[
            RetentionRule(source_tf="5m", target_tf="15m", after_days=0, batch_size=50)
        ])
        with patch("retention_service.ist_now_naive",
                   return_value=datetime(2026, 7, 1, 10, 0, 0)):
            self.service.run_cycle()
            self.assertEqual(self.service._rows_converted, 0)

    def test_get_stats(self):
        stats = self.service.get_stats()
        self.assertIn("running", stats)
        self.assertIn("last_run", stats)
        self.assertIn("rows_converted", stats)
        self.assertIn("errors", stats)
        self.assertIn("policy_rules", stats)

    def test_get_stats_policy_count(self):
        stats = self.service.get_stats()
        self.assertGreater(stats["policy_rules"], 0)


if __name__ == "__main__":
    unittest.main()
