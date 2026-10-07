"""Regression tests for the intraday candle VOLUME aggregation fix.

Bug: `Live5mBuilder.process_tick` grew the bucket with `forming["volume"] +=
volume`, where `volume` is populated from AngelOne's `last_traded_quantity` —
a CUMULATIVE quantity in this feed. Each 5m bucket therefore stored
~(ticks_in_bucket x day_volume); observed live: 410,700,652 on a day whose total
volume was ~3.7M shares.

Fix: when the caller supplies the CUMULATIVE day volume (`day_volume` =
`volume_trade_for_the_day`), grow the bucket by its DELTA only. This is also
idempotent, so a replayed tick (WS fast path + poller→aggregator bridge) can
never double-count. Omitting `day_volume` preserves the legacy per-tick
behaviour (existing tests rely on that).
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from aggregator import Live5mBuilder


def _ist_epoch(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, 0).timestamp() + 19800  # +05:30


class TestCandleVolumeDelta(unittest.TestCase):
    def setUp(self):
        self.builder = Live5mBuilder()

    def tearDown(self):
        self.builder._flush_worker_running = False
        if self.builder._flush_thread.is_alive():
            self.builder._flush_thread.join(timeout=3)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_bucket_volume_uses_day_volume_delta(self, _td, _mh):
        ts = _ist_epoch(2026, 7, 1, 10, 0)
        # cumulative day volume: 1000 -> 1000 (repeat) -> 1400 -> 1900
        self.builder.process_tick("SBIN", 100.0, tick_ts=ts, day_volume=1000)
        self.builder.process_tick("SBIN", 101.0, tick_ts=ts + 1, day_volume=1000)  # +0
        self.builder.process_tick("SBIN", 102.0, tick_ts=ts + 2, day_volume=1400)  # +400
        res = self.builder.process_tick("SBIN", 103.0, tick_ts=ts + 3, day_volume=1900)  # +500
        # baseline contributes 0, then 0 + 400 + 500
        self.assertEqual(res["5m"]["volume"], 900)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_repeated_tick_is_idempotent(self, _td, _mh):
        ts = _ist_epoch(2026, 7, 1, 10, 0)
        self.builder.process_tick("SBIN", 100.0, tick_ts=ts, day_volume=1000)
        self.builder.process_tick("SBIN", 100.0, tick_ts=ts + 1, day_volume=1500)
        # identical cumulative value replayed (WS + poller bridge) -> no change
        res = self.builder.process_tick("SBIN", 100.0, tick_ts=ts + 2, day_volume=1500)
        self.assertEqual(res["5m"]["volume"], 500)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_session_reset_does_not_dump_whole_day(self, _td, _mh):
        ts = _ist_epoch(2026, 7, 1, 10, 0)
        self.builder.process_tick("SBIN", 100.0, tick_ts=ts, day_volume=5_000_000)
        res = self.builder.process_tick("SBIN", 100.0, tick_ts=ts + 1, day_volume=5_000_000)
        self.assertEqual(res["5m"]["volume"], 0)
        # next session: cumulative counter resets small -> must not add ~5M
        ts2 = _ist_epoch(2026, 7, 2, 10, 0)
        res2 = self.builder.process_tick("SBIN", 100.0, tick_ts=ts2, day_volume=2000)
        self.assertEqual(res2["5m"]["volume"], 0)
        res3 = self.builder.process_tick("SBIN", 100.0, tick_ts=ts2 + 1, day_volume=5000)
        self.assertEqual(res3["5m"]["volume"], 3000)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_large_day_volume_no_longer_inflates(self, _td, _mh):
        # Simulate the live bug shape: ~120 cumulative ticks around ~3.6M.
        ts = _ist_epoch(2026, 7, 1, 11, 10)
        self.builder.process_tick("SBIN", 959.95, tick_ts=ts, day_volume=3_600_000)
        for i in range(1, 120):
            self.builder.process_tick("SBIN", 959.95, tick_ts=ts + i, day_volume=3_600_000 + i * 100)
        snap = self.builder.get_current("SBIN")
        # incremental volume only (119 * 100), nowhere near the cumulative sum
        self.assertEqual(snap["5m"]["volume"], 119 * 100)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_legacy_volume_still_works_without_day_volume(self, _td, _mh):
        ts = _ist_epoch(2026, 7, 1, 10, 0)
        self.builder.process_tick("SBIN", 100.0, volume=100, tick_ts=ts)
        res = self.builder.process_tick("SBIN", 100.0, volume=50, tick_ts=ts + 1)
        self.assertEqual(res["5m"]["volume"], 150)


if __name__ == "__main__":
    unittest.main()
