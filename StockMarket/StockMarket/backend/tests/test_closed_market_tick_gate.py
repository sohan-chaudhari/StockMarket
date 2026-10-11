"""Regression tests: stale broker ticks must not be BROADCAST to clients.

The AngelOne websocket replays stale snapshots for some tokens. Candle formation
already refused them (both `_on_angel_tick`'s aggregator feed and
`_poller_aggregator_bridge` apply `_tick_is_current_session`), but the tick was
still written to `_angel_tick_buffer` -- which is exactly what
`_broadcast_angel_ticks` pushes to every websocket client. So a closed market
kept visibly moving (ticker strip, minichart, Top Gainers/Losers rows) long
after the session ended.

Fix: gate the BUFFER (and its index-alias mirrors) on the same session
predicate. `latest_ticks` still records the tick and the liveness stamps are
untouched, so the live-price readers, connection-health reporting and the
market-hours-only watchdog keep their existing, tested contracts.
"""
import time
import unittest
from unittest.mock import MagicMock, patch

import main

TICKER = "ZZZGATETEST"


class ClosedMarketTickGateTests(unittest.TestCase):
    def setUp(self):
        self.buf_backup = dict(main._angel_tick_buffer)
        main._angel_tick_buffer.clear()
        with main.angelone_service.latest_ticks_lock:
            self.lt_backup = dict(main.angelone_service.latest_ticks)
            main.angelone_service.latest_ticks.clear()
        self.ts_backup = dict(main._last_angel_ts_per_ticker)
        self.proc = MagicMock()
        self._patchers = [
            patch.object(main.candle_aggregator, "process_tick", self.proc),
            # Closed market (the reported situation): only a provably
            # earlier-session timestamp is rejected.
            patch.object(main, "is_market_open_now", lambda *a, **k: False),
            # ...but on a TRADING day, so the per-tick session gate is what is
            # under test here (non-trading days are covered separately below).
            patch.object(main, "is_trading_day_now", lambda *a, **k: True),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._restore)

    def _restore(self):
        for p in self._patchers:
            p.stop()
        main._angel_tick_buffer.clear()
        main._angel_tick_buffer.update(self.buf_backup)
        with main.angelone_service.latest_ticks_lock:
            main.angelone_service.latest_ticks.clear()
            main.angelone_service.latest_ticks.update(self.lt_backup)
        main._last_angel_ts_per_ticker.clear()
        main._last_angel_ts_per_ticker.update(self.ts_backup)

    @staticmethod
    def _tick(ts):
        return {"current_price": 101.5, "prev_close": 100.0, "open": 100.0,
                "high": 102.0, "low": 99.5, "volume": 5000, "tick_volume": 10,
                "_source": "angel_ws", "_ts": ts}

    def test_previous_session_tick_is_not_broadcast(self):
        main._on_angel_tick(TICKER, self._tick(time.time() - 86400))
        self.assertNotIn(TICKER, main._angel_tick_buffer,
                         "a previous-session tick must not reach the client broadcast")
        self.assertNotIn(TICKER, main._angel_tick_buffer,
                         "and must not linger for the next 200ms flush either")

    def test_previous_session_tick_is_still_stored_for_live_prices(self):
        # Deliberate, unchanged contract: latest_ticks records it and the
        # live-price readers apply the same session gate themselves.
        main._on_angel_tick(TICKER, self._tick(time.time() - 86400))
        self.assertIn(TICKER, main.angelone_service.latest_ticks)
        self.proc.assert_not_called()

    def test_previous_session_index_tick_does_not_mirror_to_aliases(self):
        main._on_angel_tick("NIFTY", self._tick(time.time() - 86400))
        self.assertNotIn("NIFTY", main._angel_tick_buffer)
        self.assertNotIn("NIFTY50", main._angel_tick_buffer)

    def test_current_session_tick_is_broadcast(self):
        main._on_angel_tick(TICKER, self._tick(time.time()))
        self.assertIn(TICKER, main._angel_tick_buffer)
        self.assertIn(TICKER, main.angelone_service.latest_ticks)
        self.proc.assert_called_once()

    def test_current_session_index_tick_mirrors_to_aliases(self):
        main._on_angel_tick("NIFTY", self._tick(time.time()))
        self.assertIn("NIFTY", main._angel_tick_buffer)
        self.assertIn("NIFTY50", main._angel_tick_buffer)

    def test_tick_without_exchange_timestamp_is_trusted(self):
        # Staleness cannot be proven -> must still flow (delayed real ticks).
        t = self._tick(time.time())
        del t["_ts"]
        main._on_angel_tick(TICKER, t)
        self.assertIn(TICKER, main._angel_tick_buffer)

    def test_liveness_stamps_are_deliberately_unchanged(self):
        # Connection-health / watchdog semantics must not shift.
        before = main._last_angel_ts_per_ticker.get(TICKER, 0.0)
        main._on_angel_tick(TICKER, self._tick(time.time() - 86400))
        self.assertGreater(main._last_angel_ts_per_ticker[TICKER], before)
        self.assertGreater(main._last_angel_tick_time, 0)

    def test_gate_matches_the_helper_used_by_the_readers(self):
        # Same predicate as /api/market-movers and /api/live-prices, so all
        # consumers agree about what counts as a live quote.
        self.assertFalse(main._tick_is_current_session({"_exch_ts": time.time() - 86400}, False))
        self.assertTrue(main._tick_is_current_session({"_exch_ts": time.time()}, False))

    def test_non_trading_day_publishes_nothing_even_without_a_timestamp(self):
        # Weekend/holiday: the broker's replayed packets often carry NO exchange
        # timestamp, so per-tick staleness cannot be proven. No quote received on
        # such a day can be a live price, so nothing may be published.
        with patch.object(main, "is_trading_day_now", lambda *a, **k: False):
            t = self._tick(time.time())
            del t["_ts"]
            main._on_angel_tick(TICKER, t)
        self.assertNotIn(TICKER, main._angel_tick_buffer)
        self.proc.assert_not_called()

    def test_trading_day_outside_hours_still_publishes(self):
        # Pre-open / post-close on a TRADING day must keep flowing so the prices
        # settle (is_market_open_now() is False there too).
        main._on_angel_tick(TICKER, self._tick(time.time()))
        self.assertIn(TICKER, main._angel_tick_buffer)


if __name__ == "__main__":
    unittest.main()
