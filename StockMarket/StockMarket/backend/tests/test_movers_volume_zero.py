"""Regression tests: Top Gainers / Top Losers rendered "Volume: 0".

Root cause (four independent paths, all now fixed):
  1. angelone_service._handle_ws_tick preserved open/high/low/prev_close when an
     LTP-only packet omitted them, but NOT the cumulative day volume -- so the
     day's volume was overwritten with 0.
  2. main._on_angel_tick published that 0 into latest_ticks and the WS buffer.
  3. main._get_all_market_prices used `tick_data.get("volume", base)` -- a
     present-but-zero key never fell back to the baseline/DB day volume.
  4. frontend dashboard.js rendered volume when `Number(d.volume) >= 0`, so a 0
     tick wiped a volume already drawn from the baseline.

These tests are deterministic: no provider, no DB, no network.
"""
import os
import re
import threading
import unittest
from unittest.mock import patch

import main
from angelone_service import AngelOneService

FRONTEND_DASHBOARD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "dashboard.js")


class _ProviderBase(unittest.TestCase):
    """A private AngelOneService with no live session."""
    def setUp(self):
        self.svc = AngelOneService()
        self.svc.token_to_ticker_map = {"999": "RELIANCE"}
        self.svc.latest_ticks = {}
        self.svc.latest_ticks_lock = threading.Lock()
        self.svc._last_tick_fingerprint = {}
        self.svc._duplicate_tick_count = 0
        self.received = []
        self.svc.on_tick_callback = lambda ticker, data: self.received.append((ticker, data))

    @staticmethod
    def _quote(price_paise, ts, qty, day_volume):
        return {
            "token": "999",
            "last_traded_price": price_paise,
            "exchange_timestamp": ts,
            "last_traded_quantity": qty,
            "volume_trade_for_the_day": day_volume,
        }

    @staticmethod
    def _ltp_only(price_paise, ts, qty):
        """LTP packet: no volume_trade_for_the_day / OHLC fields at all."""
        return {
            "token": "999",
            "last_traded_price": price_paise,
            "exchange_timestamp": ts,
            "last_traded_quantity": qty,
        }


class WsTickVolumePreservationTests(_ProviderBase):
    def test_ltp_only_packet_keeps_the_last_known_day_volume(self):
        # previous full quote established the day's cumulative volume
        self.svc.latest_ticks["RELIANCE"] = {"volume": 100000, "open": 1500.0}
        self.svc._handle_ws_tick(self._ltp_only(150500, 1780000005, 7))
        self.assertEqual(len(self.received), 1)
        _ticker, data = self.received[0]
        self.assertEqual(data.get("volume"), 100000,
                         "an LTP-only packet must not zero the day volume")

    def test_a_new_full_quote_updates_the_volume(self):
        self.svc.latest_ticks["RELIANCE"] = {"volume": 100000}
        self.svc._handle_ws_tick(self._quote(150500, 1780000005, 7, 123456))
        _ticker, data = self.received[0]
        self.assertEqual(data.get("volume"), 123456)

    def test_ltp_only_with_no_prior_volume_stays_zero(self):
        # nothing known yet -> nothing to preserve (must not invent a value)
        self.svc._handle_ws_tick(self._ltp_only(150500, 1780000005, 7))
        _ticker, data = self.received[0]
        self.assertEqual(data.get("volume"), 0)


class OnAngelTickVolumePreservationTests(unittest.TestCase):
    TICKER = "ZZZVOLTEST"

    def setUp(self):
        self.buf_backup = dict(main._angel_tick_buffer)
        main._angel_tick_buffer.clear()
        with main.angelone_service.latest_ticks_lock:
            self.lt_backup = main.angelone_service.latest_ticks.get(self.TICKER)
            main.angelone_service.latest_ticks[self.TICKER] = {"volume": 42000, "current_price": 101.0}
        self._agg = patch.object(main.candle_aggregator, "process_tick", side_effect=lambda *a, **k: None)
        self._agg.start()
        # Deterministic regardless of the real weekday: the publish path now also
        # requires a trading day, so pin it (this class tests volume, not gating).
        self._td = patch.object(main, "is_trading_day_now", lambda *a, **k: True)
        self._td.start()

    def tearDown(self):
        self._agg.stop()
        self._td.stop()
        main._angel_tick_buffer.clear()
        main._angel_tick_buffer.update(self.buf_backup)
        with main.angelone_service.latest_ticks_lock:
            if self.lt_backup is None:
                main.angelone_service.latest_ticks.pop(self.TICKER, None)
            else:
                main.angelone_service.latest_ticks[self.TICKER] = self.lt_backup

    def _tick(self, volume):
        return {
            "current_price": 101.5, "prev_close": 100.0, "open": 100.0,
            "high": 102.0, "low": 99.5, "volume": volume,
            "tick_volume": 10, "_source": "angel_ws",
        }

    def test_zero_volume_tick_preserves_the_previous_volume(self):
        main._on_angel_tick(self.TICKER, self._tick(0))
        self.assertEqual(main._angel_tick_buffer[self.TICKER]["volume"], 42000)

    def test_real_volume_tick_is_used_as_is(self):
        main._on_angel_tick(self.TICKER, self._tick(77777))
        self.assertEqual(main._angel_tick_buffer[self.TICKER]["volume"], 77777)


class MarketPricesVolumeFallbackTests(unittest.TestCase):
    """`_get_all_market_prices` must fall back to the baseline day volume."""
    TICKER = "ZZZVOLMKT"

    def setUp(self):
        self.base_backup = main._db_baseline_prices
        main._db_baseline_prices = {
            self.TICKER: {"current": 100.0, "current_price": 100.0, "prev_close": 99.0,
                          "open": 99.0, "high": 101.0, "low": 98.0, "volume": 55555,
                          "prev_volume": 40000},
        }
        with main.angelone_service.latest_ticks_lock:
            self.lt_backup = dict(main.angelone_service.latest_ticks)
            main.angelone_service.latest_ticks.clear()
        self._sess = patch.object(main, "_tick_is_current_session", return_value=True)
        self._sess.start()

    def tearDown(self):
        self._sess.stop()
        main._db_baseline_prices = self.base_backup
        with main.angelone_service.latest_ticks_lock:
            main.angelone_service.latest_ticks.clear()
            main.angelone_service.latest_ticks.update(self.lt_backup)

    def _prices_with_tick(self, volume):
        with main.angelone_service.latest_ticks_lock:
            main.angelone_service.latest_ticks[self.TICKER] = {
                "current_price": 105.0, "prev_close": 100.0, "open": 100.0,
                "high": 106.0, "low": 99.0, "volume": volume, "_source": "angel_ws",
            }
        return main._get_all_market_prices()

    def test_zero_volume_tick_falls_back_to_baseline(self):
        prices = self._prices_with_tick(0)
        self.assertEqual(prices[self.TICKER]["volume"], 55555,
                         "a present-but-zero tick volume must not hide the baseline volume")

    def test_real_volume_tick_wins(self):
        prices = self._prices_with_tick(88888)
        self.assertEqual(prices[self.TICKER]["volume"], 88888)


class FrontendVolumeGuardTests(unittest.TestCase):
    def test_dashboard_requires_positive_volume(self):
        with open(FRONTEND_DASHBOARD, "r", encoding="utf-8") as f:
            src = f.read()
        # the volume renderer must not accept 0
        self.assertRegex(src, r"Number\(d\.volume\)\s*>\s*0")
        self.assertNotRegex(src, r"Number\(d\.volume\)\s*>=\s*0")


if __name__ == "__main__":
    unittest.main()
