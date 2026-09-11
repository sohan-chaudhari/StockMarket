"""
Decision 3 — duplicate-tick volume dedup, verified at the ingestion point.

AngelOne's WS "Quote" mode payload has no unique per-trade ID (confirmed by
reading _handle_ws_tick's field extraction: token, ltp, exchange_timestamp,
day OHLC, day volume, last-traded quantity only) — so the fix uses a
composite (exchange_ts, price, qty) fingerprint per ticker, checked before a
tick is forwarded to the aggregator via on_tick_callback.
"""
import unittest
import threading
from angelone_service import AngelOneService


class TestTickDeduplication(unittest.TestCase):
    def setUp(self):
        self.svc = AngelOneService()
        # Isolate this test from the process-wide singleton's state.
        self.svc.token_to_ticker_map = {"999": "RELIANCE"}
        self.svc.latest_ticks = {}
        self.svc.latest_ticks_lock = threading.Lock()
        self.svc._last_tick_fingerprint = {}
        self.svc._duplicate_tick_count = 0
        self.received = []
        self.svc.on_tick_callback = lambda ticker, data: self.received.append((ticker, data))

    def _tick(self, price_paise, ts, qty):
        return {
            "token": "999",
            "last_traded_price": price_paise,
            "exchange_timestamp": ts,
            "last_traded_quantity": qty,
            "volume_trade_for_the_day": 100000,
        }

    def test_first_tick_is_forwarded(self):
        self.svc._handle_ws_tick(self._tick(150000, 1780000000, 10))
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.svc._duplicate_tick_count, 0)

    def test_exact_replay_is_not_forwarded(self):
        tick = self._tick(150000, 1780000000, 10)
        self.svc._handle_ws_tick(tick)
        self.svc._handle_ws_tick(dict(tick))  # simulate a WS reconnect replay
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.svc._duplicate_tick_count, 1)

    def test_replay_does_not_double_count_when_fed_into_aggregator(self):
        # End-to-end: the volume the aggregator would accumulate must reflect
        # only the forwarded (deduplicated) ticks, not the raw WS message count.
        from aggregator import Live5mBuilder
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch
        builder = Live5mBuilder()
        try:
            def feed(ticker, data):
                builder.process_tick(ticker, data["current_price"], data["tick_volume"],
                                      tick_ts=data["_ts"])
            self.svc.on_tick_callback = feed

            # Fixed weekday, mid-session IST timestamp. process_tick treats a
            # tick_ts more than 30s behind the real wall clock as "frozen" and
            # substitutes the actual server time — patch aggregator's clock to
            # match so the fixed timestamp is honored regardless of when this
            # test suite actually runs (same pattern the existing aggregator
            # tests use for is_market_hour/is_trading_day).
            ist = timezone(timedelta(hours=5, minutes=30))
            mid_session_dt = datetime(2026, 7, 1, 10, 0, 0, tzinfo=ist)
            mid_session = int(mid_session_dt.timestamp())
            naive_now = mid_session_dt.replace(tzinfo=None)

            tick = self._tick(150000, mid_session, 10)
            with patch("aggregator.ist_now_naive", return_value=naive_now), \
                 patch("time.time", return_value=mid_session):
                self.svc._handle_ws_tick(tick)
                self.svc._handle_ws_tick(dict(tick))  # replay — must be swallowed before the aggregator

            current = builder.get_current("RELIANCE")
            self.assertIsNotNone(current)
            self.assertEqual(current["5m"]["volume"], 10)  # not 20
        finally:
            builder._flush_worker_running = False

    def test_new_price_after_duplicate_is_still_forwarded(self):
        tick = self._tick(150000, 1780000000, 10)
        self.svc._handle_ws_tick(tick)
        self.svc._handle_ws_tick(dict(tick))  # duplicate, swallowed
        next_tick = self._tick(150500, 1780000005, 5)  # genuinely new trade
        self.svc._handle_ws_tick(next_tick)
        self.assertEqual(len(self.received), 2)

    def test_same_timestamp_different_price_is_not_treated_as_duplicate(self):
        # Two distinct trades in the same second at different prices must both
        # be forwarded — the fingerprint requires price+qty to match too.
        self.svc._handle_ws_tick(self._tick(150000, 1780000000, 10))
        self.svc._handle_ws_tick(self._tick(150100, 1780000000, 10))
        self.assertEqual(len(self.received), 2)

    def test_dedup_state_persists_across_a_reconnect(self):
        # The fingerprint cache lives on the service singleton, not the socket,
        # so it must still catch a replay "after" a simulated reconnect (i.e.
        # nothing in the reconnect path resets it).
        tick = self._tick(150000, 1780000000, 10)
        self.svc._handle_ws_tick(tick)
        # Simulate reconnect: only ws_connected/sws state would change, not
        # the fingerprint cache — assert it's still intact.
        self.assertIn("RELIANCE", self.svc._last_tick_fingerprint)
        self.svc._handle_ws_tick(dict(tick))
        self.assertEqual(len(self.received), 1)


if __name__ == "__main__":
    unittest.main()
