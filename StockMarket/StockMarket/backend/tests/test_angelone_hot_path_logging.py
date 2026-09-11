"""Batch 4 (pre-AWS hardening) tests: no unbounded per-tick print() in
angelone_service.py's hot path.

Before this fix, _handle_ws_tick unconditionally called print() every time
an incoming tick had a missing/zero exchange_timestamp -- a real risk given
AngelOne's WS alternates full-quote and LTP-only packets (the latter
commonly omit exchange_timestamp), at potentially thousands of ticks/sec
across the subscribed universe during market hours.

angelone_service.py has no heavy import-time side effects (AngelOneService()
is directly instantiable without connecting), so this imports it directly,
matching test_angelone_tick_dedup.py's existing pattern.
"""
import time
import unittest
from unittest.mock import patch

from angelone_service import AngelOneService, _EXCH_TS_FALLBACK_LOG_INTERVAL


class HotPathLoggingTestBase(unittest.TestCase):
    def setUp(self):
        self.svc = AngelOneService()
        self.svc.token_to_ticker_map = {"999": "RELIANCE"}
        self.svc.latest_ticks = {}
        import threading
        self.svc.latest_ticks_lock = threading.Lock()
        self.svc._last_tick_fingerprint = {}
        self.svc._duplicate_tick_count = 0
        self.svc._exch_ts_fallback_count = 0
        self.svc._exch_ts_fallback_last_log = 0.0
        self.received = []
        self.svc.on_tick_callback = lambda ticker, data: self.received.append((ticker, data))

    def _tick(self, price_paise=150000, exch_ts=-1, qty=10, token="999"):
        # NOTE: exch_ts=-1, not 0. _handle_ws_tick's field extraction is
        # `msg.get('exchange_timestamp') or msg.get('exch_timestamp') or ...`
        # -- a literal 0 is falsy in Python, so it gets silently collapsed to
        # None by that `or` chain *before* the `if exch_ts <= 0` branch under
        # test is ever reached (float(None) raises TypeError instead, which
        # is caught by a separate except clause that was never the printing
        # one). A negative number is truthy, so it survives the `or` chain
        # and correctly reaches float(exch_ts) <= 0 -- the actual branch that
        # used to print() on every occurrence. This is pre-existing behavior,
        # unrelated to and unchanged by the Batch 4 logging fix.
        return {
            "token": token,
            "last_traded_price": price_paise,
            "exchange_timestamp": exch_ts,
            "last_traded_quantity": qty,
            "volume_trade_for_the_day": 100000,
        }


class TestNoPerTickPrint(HotPathLoggingTestBase):
    def test_no_print_for_a_single_zero_timestamp_tick(self):
        with patch("builtins.print") as mock_print:
            self.svc._handle_ws_tick(self._tick(exch_ts=-1))
        mock_print.assert_not_called()

    def test_no_print_across_many_zero_timestamp_ticks(self):
        """The original bug: this exact scenario (a burst of LTP-only ticks
        with no exchange_timestamp) would previously print() on every one."""
        with patch("builtins.print") as mock_print:
            for i in range(200):
                # Vary price/qty so dedup doesn't swallow them before reaching
                # the exchange_timestamp branch under test.
                self.svc._handle_ws_tick(self._tick(price_paise=150000 + i, exch_ts=-1, qty=1))
        mock_print.assert_not_called()

    def test_callback_error_does_not_print(self):
        self.svc.on_tick_callback = lambda ticker, data: (_ for _ in ()).throw(RuntimeError("boom"))
        with patch("builtins.print") as mock_print:
            self.svc._handle_ws_tick(self._tick(exch_ts=1780000000))
        mock_print.assert_not_called()

    def test_tick_handler_exception_does_not_print(self):
        self.svc.token_to_ticker_map = None  # forces an AttributeError inside the try block
        with patch("builtins.print") as mock_print:
            self.svc._handle_ws_tick(self._tick())
        mock_print.assert_not_called()


class TestCumulativeCounterAndRateLimit(HotPathLoggingTestBase):
    def test_counter_increments_on_every_occurrence_even_when_log_is_rate_limited(self):
        for i in range(50):
            self.svc._handle_ws_tick(self._tick(price_paise=150000 + i, exch_ts=-1, qty=1))
        # The counter is the always-cheap diagnostic -- must reflect every
        # occurrence regardless of how often the log line itself fires.
        self.assertEqual(self.svc._exch_ts_fallback_count, 50)

    def test_counter_not_incremented_for_healthy_ticks(self):
        self.svc._handle_ws_tick(self._tick(exch_ts=1780000000))
        self.assertEqual(self.svc._exch_ts_fallback_count, 0)

    def test_debug_log_fires_at_most_once_within_the_rate_limit_window(self):
        with patch("angelone_service.logger") as mock_logger:
            for i in range(100):
                self.svc._handle_ws_tick(self._tick(price_paise=150000 + i, exch_ts=-1, qty=1))
        # All 100 calls happen within a fraction of a second in a test --
        # well inside _EXCH_TS_FALLBACK_LOG_INTERVAL (60s) -- so debug()
        # must have fired exactly once (the first occurrence), not 100 times.
        self.assertEqual(mock_logger.debug.call_count, 1)

    def test_debug_log_fires_again_after_the_window_elapses(self):
        with patch("angelone_service.logger") as mock_logger:
            self.svc._handle_ws_tick(self._tick(exch_ts=-1))
            self.assertEqual(mock_logger.debug.call_count, 1)
            # Simulate the rate-limit window having elapsed.
            self.svc._exch_ts_fallback_last_log -= (_EXCH_TS_FALLBACK_LOG_INTERVAL + 1)
            self.svc._handle_ws_tick(self._tick(exch_ts=-1))
            self.assertEqual(mock_logger.debug.call_count, 2)

    def test_debug_log_uses_lazy_percent_style_args_not_a_preformatted_string(self):
        """Proves the fix actually avoids the formatting cost when DEBUG is
        disabled: the message must be a %-style template with the ticker
        passed as a separate arg, not an f-string interpolated eagerly."""
        with patch("angelone_service.logger") as mock_logger:
            self.svc._handle_ws_tick(self._tick(exch_ts=-1))
        args, _kwargs = mock_logger.debug.call_args
        template = args[0]
        self.assertIn("%s", template)
        self.assertIn("%d", template)
        self.assertNotIn("RELIANCE", template)  # not already interpolated
        self.assertIn("RELIANCE", args)  # passed as a lazy arg instead

    def test_debug_uses_actual_logging_level_not_print_semantics(self):
        """Confirms this now genuinely goes through logging's level-gating --
        with the logger disabled below DEBUG, no formatting work happens and
        no record is emitted (the whole point of switching off print())."""
        import logging
        logger = logging.getLogger("angelone_service")
        original_level = logger.level
        logger.setLevel(logging.WARNING)
        try:
            with patch.object(logger, "handle") as mock_handle:
                for i in range(20):
                    self.svc._handle_ws_tick(self._tick(price_paise=150000 + i, exch_ts=-1, qty=1))
                mock_handle.assert_not_called()
        finally:
            logger.setLevel(original_level)  # don't leak level changes into other tests


class TestExceptionPathsUseAppropriateLevels(HotPathLoggingTestBase):
    def test_callback_error_logged_at_warning(self):
        self.svc.on_tick_callback = lambda ticker, data: (_ for _ in ()).throw(RuntimeError("boom"))
        with patch("angelone_service.logger") as mock_logger:
            self.svc._handle_ws_tick(self._tick(exch_ts=1780000000))
        mock_logger.warning.assert_called_once()
        args, _ = mock_logger.warning.call_args
        self.assertIn("RELIANCE", args)

    def test_tick_handler_error_logged_at_error_level(self):
        self.svc.token_to_ticker_map = None  # forces an AttributeError
        with patch("angelone_service.logger") as mock_logger:
            self.svc._handle_ws_tick(self._tick(token="999"))
        mock_logger.error.assert_called_once()
        args, _ = mock_logger.error.call_args
        self.assertIn("999", args)


class TestMarketDataBehaviorUnchanged(HotPathLoggingTestBase):
    """Regression guard: the logging change must not alter what value ends
    up in latest_ticks / the callback payload."""

    def test_zero_timestamp_still_falls_back_to_server_time(self):
        before = time.time()
        self.svc._handle_ws_tick(self._tick(exch_ts=-1))
        after = time.time()

        self.assertEqual(len(self.received), 1)
        _, data = self.received[0]
        self.assertGreaterEqual(data["_ts"], before)
        self.assertLessEqual(data["_ts"], after)

    def test_healthy_timestamp_is_used_unmodified(self):
        self.svc._handle_ws_tick(self._tick(exch_ts=1780000000))
        _, data = self.received[0]
        self.assertEqual(data["_ts"], 1780000000)

    def test_price_still_forwarded_correctly_alongside_fallback(self):
        self.svc._handle_ws_tick(self._tick(price_paise=250050, exch_ts=-1))
        _, data = self.received[0]
        self.assertEqual(data["current_price"], 2500.5)


if __name__ == "__main__":
    unittest.main()
