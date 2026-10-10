"""Regression tests for the 1W/1M closed-market backfill failure loop.

Verified production symptom (read-only forensics, release 0874683):
  * `[HISTORICAL] API error: Invalid Token` -> session invalidated
  * `[HISTORICAL] [ERROR] Not logged in. Call login() first.`  x423 / 20 min
  * 32 of 33 batches inserted 0 rows, every ~82 s, all weekend

Two independent defects:
  1. `_phase2_fetch_into_stock_data` could not tell "auth failure" from "no
     data" (`get_historical_candles` returns [] in both cases), so it kept
     issuing one doomed request per remaining ticker * date-chunk.
  2. `is_market_open_or_opening_soon()` returned False on a NON-trading day.
     Both call sites use it as a PAUSE predicate, so the worker never paused at
     the weekend.

These tests are deterministic: the provider and the clock are mocked.
"""
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import backfill_weekly_monthly as bwm
import historical_service as historical_service_mod
from exchange_calendar import nse_calendar


def _freeze(when: datetime):
    """A `datetime` stand-in whose now() always returns `when`."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    return _Frozen


def _fake_provider(is_logged_in=True, fetch=None):
    """Minimal stand-in for historical_service.historical_service."""
    svc = SimpleNamespace(is_logged_in=is_logged_in, login=lambda: True)
    svc.get_historical_candles = fetch or (lambda **kw: [])
    return svc


class _Base(unittest.TestCase):
    def setUp(self):
        self._attempted = set(bwm._attempted_tickers)
        bwm._attempted_tickers.clear()
        # the real loop sleeps between chunks/tickers -- keep tests instant
        self._sleep = patch.object(bwm.time, "sleep", lambda *_: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        bwm._attempted_tickers.clear()
        bwm._attempted_tickers.update(self._attempted)


# ── 1. an auth failure aborts the batch immediately ─────────────────────────

class AuthFailureAbortsBatchTests(_Base):
    def test_aborts_after_the_first_call_not_every_ticker_and_chunk(self):
        calls = []

        def _fetch(**kw):
            calls.append(kw["ticker"])
            # real behaviour: the auth error flips is_logged_in to False
            provider.is_logged_in = False
            return []

        provider = _fake_provider(fetch=_fetch)
        with patch.object(historical_service_mod, "historical_service", provider):
            with self.assertRaises(bwm.HistoricalAuthError):
                bwm._phase2_fetch_into_stock_data(
                    from_d=date(2021, 1, 1), to_d=date(2024, 1, 1),
                    missing_tickers=[("AAA",), ("BBB",), ("CCC",), ("DDD",), ("EEE",)],
                    label="test",
                )
        # one call for the first ticker only -- no further tickers, no further
        # 365-day chunks (the 3-year range would otherwise be 3 calls/ticker)
        self.assertEqual(calls, ["AAA"])

    def test_only_one_call_is_made_before_aborting(self):
        calls = []

        def _fetch(**kw):
            calls.append(kw["ticker"])
            return []

        provider = _fake_provider(is_logged_in=True, fetch=_fetch)

        def _dead_fetch(**kw):
            calls.append(kw["ticker"])
            provider.is_logged_in = False
            return []

        provider.get_historical_candles = _dead_fetch
        with patch.object(historical_service_mod, "historical_service", provider):
            with self.assertRaises(bwm.HistoricalAuthError):
                bwm._phase2_fetch_into_stock_data(
                    from_d=date(2021, 1, 1), to_d=date(2024, 1, 1),
                    missing_tickers=[("AAA",)], label="t")
        self.assertEqual(len(calls), 1)


# ── 2. a dead session does not create a rapid retry loop ────────────────────

class NoRapidRetryLoopTests(_Base):
    def _batch(self, provider):
        with patch.object(bwm, "is_market_open_or_opening_soon", return_value=False), \
             patch.object(bwm, "_tickers_missing_1w_coverage",
                          return_value=[("AAA",), ("BBB",)]), \
             patch.object(bwm, "_tickers_missing_1m_coverage", return_value=[]), \
             patch.object(historical_service_mod, "historical_service", provider):
            return bwm.run_controlled_closed_backfill_batch(batch_size=5)

    def test_batch_reports_bounded_backoff_not_the_60s_cadence(self):
        calls = []

        def _fetch(**kw):
            calls.append(kw["ticker"])
            provider.is_logged_in = False
            return []

        provider = _fake_provider(fetch=_fetch)
        res = self._batch(provider)
        self.assertEqual(res["status"], "auth_failed")
        self.assertEqual(res["processed"], 0)
        self.assertEqual(res["remaining"], 2)
        self.assertEqual(res["retry_after"], bwm.AUTH_FAILURE_BACKOFF_SEC)
        self.assertGreaterEqual(res["retry_after"], 300)
        # one doomed call, not one per ticker
        self.assertEqual(calls, ["AAA"])

    def test_repeated_dead_session_batches_stay_bounded(self):
        for _ in range(3):
            calls = []

            def _fetch(**kw):
                calls.append(kw["ticker"])
                provider.is_logged_in = False
                return []

            provider = _fake_provider(fetch=_fetch)
            res = self._batch(provider)
            self.assertEqual(res["status"], "auth_failed")
            self.assertEqual(len(calls), 1)


# ── 3. failed tickers are never recorded as processed ───────────────────────

class NotFalselyMarkedProcessedTests(_Base):
    def test_auth_failed_batch_leaves_tickers_unmarked(self):
        provider = _fake_provider(fetch=lambda **kw: [])
        provider.get_historical_candles = lambda **kw: (
            setattr(provider, "is_logged_in", False) or [])
        with patch.object(bwm, "is_market_open_or_opening_soon", return_value=False), \
             patch.object(bwm, "_tickers_missing_1w_coverage",
                          return_value=[("AAA",), ("BBB",)]), \
             patch.object(bwm, "_tickers_missing_1m_coverage", return_value=[]), \
             patch.object(historical_service_mod, "historical_service", provider):
            res = bwm.run_controlled_closed_backfill_batch(batch_size=5)
        self.assertEqual(res["status"], "auth_failed")
        self.assertNotIn(("AAA", "1W"), bwm._attempted_tickers)
        self.assertNotIn(("BBB", "1W"), bwm._attempted_tickers)

    def test_successful_batch_still_marks_tickers(self):
        provider = _fake_provider(fetch=lambda **kw: [])
        with patch.object(bwm, "is_market_open_or_opening_soon", return_value=False), \
             patch.object(bwm, "_tickers_missing_1w_coverage",
                          return_value=[("AAA",), ("BBB",)]), \
             patch.object(bwm, "_tickers_missing_1m_coverage", return_value=[]), \
             patch.object(historical_service_mod, "historical_service", provider):
            res = bwm.run_controlled_closed_backfill_batch(batch_size=5)
        self.assertEqual(res["status"], "in_progress")
        self.assertIn(("AAA", "1W"), bwm._attempted_tickers)
        self.assertIn(("BBB", "1W"), bwm._attempted_tickers)


# ── 4. weekends / holidays pause; trading-day closed window runs ────────────

class PauseSchedulingTests(_Base):
    def test_non_trading_day_pauses(self):
        with patch.object(nse_calendar, "is_trading_day", return_value=False):
            self.assertTrue(bwm.is_market_open_or_opening_soon())

    def test_weekend_pauses_via_the_fallback_path(self):
        # calendar import/behaviour unavailable -> weekday fallback
        with patch.object(nse_calendar, "is_trading_day",
                          side_effect=RuntimeError("no calendar")), \
             patch.object(bwm, "datetime", _freeze(datetime(2026, 10, 10, 12, 0))):  # Saturday
            self.assertTrue(bwm.is_market_open_or_opening_soon())

    def test_trading_day_market_hours_pause(self):
        with patch.object(nse_calendar, "is_trading_day", return_value=True), \
             patch.object(bwm, "datetime", _freeze(datetime(2026, 10, 12, 10, 0))):  # Mon 10:00
            self.assertTrue(bwm.is_market_open_or_opening_soon())

    def test_trading_day_closed_window_runs(self):
        with patch.object(nse_calendar, "is_trading_day", return_value=True), \
             patch.object(bwm, "datetime", _freeze(datetime(2026, 10, 12, 20, 0))):  # Mon 20:00
            self.assertFalse(bwm.is_market_open_or_opening_soon())

    def test_batch_pauses_when_the_predicate_says_pause(self):
        with patch.object(bwm, "is_market_open_or_opening_soon", return_value=True):
            res = bwm.run_controlled_closed_backfill_batch(batch_size=5)
        self.assertEqual(res["status"], "paused_market_hours")
        self.assertEqual(res["processed"], 0)


# ── 5. ordinary transient errors keep today's behaviour ────────────────────

class TransientErrorTests(_Base):
    def test_per_ticker_error_does_not_abort_the_batch(self):
        calls = []

        def _fetch(**kw):
            calls.append(kw["ticker"])
            if kw["ticker"] == "AAA":
                raise ValueError("transient boom")
            return []

        provider = _fake_provider(fetch=_fetch)
        with patch.object(historical_service_mod, "historical_service", provider):
            out = bwm._phase2_fetch_into_stock_data(
                from_d=date(2021, 1, 1), to_d=date(2024, 1, 1),
                missing_tickers=[("AAA",), ("BBB",)], label="t")
        self.assertEqual(out, 0)
        self.assertIn("AAA", calls)
        self.assertIn("BBB", calls, "a transient error must not stop the batch")
        self.assertTrue(provider.is_logged_in)

    def test_missing_data_is_not_an_auth_failure(self):
        provider = _fake_provider(fetch=lambda **kw: [])   # session stays valid
        with patch.object(historical_service_mod, "historical_service", provider):
            out = bwm._phase2_fetch_into_stock_data(
                from_d=date(2021, 1, 1), to_d=date(2024, 1, 1),
                missing_tickers=[("AAA",), ("BBB",)], label="t")
        self.assertEqual(out, 0)
        self.assertTrue(provider.is_logged_in)


if __name__ == "__main__":
    unittest.main()
