"""Phase 2 (A2) regression: stale / out-of-session ticks must not reach the
candle aggregator.

`Live5mBuilder.process_tick` coerces any `tick_ts` older than 30s to "now", so
a replayed snapshot whose exchange timestamp proves it belongs to a PREVIOUS
session would open/update a candle in the CURRENT bucket (the residual
market-movers stale-tick issue, on the candle side). `_on_angel_tick` now gates
the aggregator feed with `_tick_is_current_session()`:

  * a previous-session quote is NOT fed to the aggregator;
  * a current-session tick IS fed;
  * the tick is still recorded in `latest_ticks` (the live-price readers apply
    their own session gate) -- the gate only guards candle formation;
  * missing / invalid exchange timestamps are trusted (the helper cannot prove
    staleness for them), so legitimate live data is never dropped.
"""
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import main
from angelone_service import angelone_service as svc

IST = main.IST
TICKER = "ZZAGGGSESSION"


def _epoch(dt):
    return dt.timestamp()


class OnAngelTickAggregatorGateTests(unittest.TestCase):
    def setUp(self):
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)
        main._last_aggregator_feed_ts.pop(TICKER, None)
        with main._angel_tick_buffer_lock:
            main._angel_tick_buffer.pop(TICKER, None)
        self.calls = []
        self._patch = patch.object(
            main.candle_aggregator, "process_tick",
            side_effect=lambda *a, **k: self.calls.append((a, k)),
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)
        main._last_aggregator_feed_ts.pop(TICKER, None)
        with main._angel_tick_buffer_lock:
            main._angel_tick_buffer.pop(TICKER, None)

    def _tick(self, exch_ts, price=100.0):
        main._on_angel_tick(TICKER, {
            "current_price": price, "prev_close": 99.0, "volume": 5,
            "_ts": exch_ts, "_source": "angel_ws",
        })

    def test_previous_session_tick_is_not_fed_to_aggregator(self):
        stale = _epoch(datetime.now(IST) - timedelta(days=1))
        self._tick(stale)
        self.assertEqual(
            self.calls, [],
            "a previous-session quote must not form a candle in the current bucket",
        )

    def test_current_session_tick_is_fed_to_aggregator(self):
        fresh = _epoch(datetime.now(IST).replace(hour=10, minute=30, second=0, microsecond=0))
        self._tick(fresh)
        self.assertEqual(len(self.calls), 1, "a current-session tick must form a candle")

    def test_stale_tick_is_still_stored_for_live_prices(self):
        # The gate only guards candle formation; latest_ticks still records the
        # tick so the live-price readers can apply their own session gate.
        stale = _epoch(datetime.now(IST) - timedelta(days=1))
        self._tick(stale)
        with svc.latest_ticks_lock:
            self.assertIn(TICKER, svc.latest_ticks)

    def test_missing_exchange_timestamp_is_trusted(self):
        # A tick with no usable exchange timestamp cannot be proven stale, so it
        # must still be fed (this is the "never discard valid live data" case).
        main._on_angel_tick(TICKER, {
            "current_price": 100.0, "prev_close": 99.0, "volume": 5,
            "_ts": 0, "_source": "angel_ws",
        })
        self.assertEqual(len(self.calls), 1)


class GatePreservesOhlcvTests(unittest.TestCase):
    """End-to-end through a REAL Live5mBuilder: a legitimate tick still builds a
    correct OHLCV candle, and a stale tick creates NO candle at all (so it can
    never corrupt the current bucket)."""

    def setUp(self):
        from aggregator import Live5mBuilder
        self.builder = Live5mBuilder()
        self._orig_agg = main.candle_aggregator
        main.candle_aggregator = self.builder
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)
        main._last_aggregator_feed_ts.pop(TICKER, None)
        with main._angel_tick_buffer_lock:
            main._angel_tick_buffer.pop(TICKER, None)

    def tearDown(self):
        main.candle_aggregator = self._orig_agg
        self.builder._flush_worker_running = False
        if self.builder._flush_thread.is_alive():
            self.builder._flush_thread.join(timeout=3)
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)
        main._last_aggregator_feed_ts.pop(TICKER, None)
        with main._angel_tick_buffer_lock:
            main._angel_tick_buffer.pop(TICKER, None)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_valid_tick_builds_correct_ohlcv(self, _td, _mh):
        in_session = datetime.now(IST).replace(hour=10, minute=30, second=0, microsecond=0)
        main._on_angel_tick(TICKER, {
            "current_price": 101.5, "prev_close": 100.0, "open": 100.0,
            "high": 102.0, "low": 99.5, "volume": 5000, "tick_volume": 10,
            "_ts": in_session.timestamp(), "_source": "angel_ws",
        })
        cur = self.builder.get_current(TICKER)
        self.assertIsNotNone(cur, "a valid tick must build a candle")
        c = cur["5m"]
        self.assertEqual((c["open"], c["high"], c["low"], c["close"]),
                         (101.5, 101.5, 101.5, 101.5))
        # first tick for the ticker: cumulative day volume is the baseline -> 0 delta
        self.assertEqual(c["volume"], 0)

    @patch("aggregator.is_market_hour", return_value=True)
    @patch("aggregator.is_trading_day", return_value=True)
    def test_stale_tick_creates_no_candle(self, _td, _mh):
        stale = datetime.now(IST) - timedelta(days=1)
        main._on_angel_tick(TICKER, {
            "current_price": 999.0, "prev_close": 100.0, "open": 100.0,
            "high": 1000.0, "low": 99.0, "volume": 5000, "tick_volume": 10,
            "_ts": stale.timestamp(), "_source": "angel_ws",
        })
        self.assertIsNone(self.builder.get_current(TICKER))
        self.assertNotIn(TICKER, self.builder.active_candles,
                         "a previous-session quote must not create a current-bucket candle")


if __name__ == "__main__":
    unittest.main()
