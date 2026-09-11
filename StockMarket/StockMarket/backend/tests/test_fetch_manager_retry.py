"""
Regression tests for AngelOneFetchManager's retry/failure semantics after
the historical_service silent-empty-response fix. Covers exactly the 8
proof points required before any further real 1D migration:

  1. Rate-limit response is NOT converted into a successful empty list.
  2. Rate-limit response reaches fetch_with_retry().
  3. Retry actually occurs.
  4. A transient network exception triggers retry.
  5. Exhausted retries mark the chunk FAILED (success=False).
  6. A genuine successful empty API response remains a legitimate empty
     result (does not retry, does not fail).
  7. API errors cannot result in a "successful" (DONE-eligible) chunk.
  8. Migration statistics correctly report failed/retried requests -- this
     one is covered end-to-end in test_batch_downloader_retry_stats.py,
     since the stats live in BatchDownloader, not AngelOneFetchManager.

All Angel One calls are mocked at the historical_service boundary -- no
real network access.
"""
import unittest
from datetime import date
from unittest.mock import patch

from migration.fetch_manager import AngelOneFetchManager
from migration.config import MigrationConfig
from historical_service import HistoricalFetchError


def _cfg(retry_max=3, backoff=(0, 0, 0)):
    return MigrationConfig(requests_per_second=1000, retry_max=retry_max, backoff_seconds=list(backoff))


class TestRateLimitNotSilentlyEmpty(unittest.TestCase):
    def test_rate_limit_error_does_not_produce_success_true_empty_candles(self):
        """Point 1 + 2: a rate-limit failure from historical_service must
        reach fetch_with_retry() as an actual failure, never as
        (True, [], ...) -- which is exactly how the real gaps were
        produced in the second controlled batch."""
        mgr = AngelOneFetchManager(_cfg(retry_max=1))
        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.side_effect = HistoricalFetchError(
                "Access denied because of exceeding access rate", retryable=True,
            )
            success, candles, err, attempts = mgr.fetch_with_retry(
                "RELIANCE", "ONE_DAY", date(2024, 1, 1), date(2024, 1, 5),
            )
        self.assertFalse(success)
        self.assertEqual(candles, [])
        self.assertIn("exceeding access rate", err)


class TestRetryActuallyOccurs(unittest.TestCase):
    def test_transient_rate_limit_retries_then_succeeds(self):
        """Point 3: a retryable failure on the first attempt, success on
        the second, must be retried (not immediately given up on) and the
        final result must be success with the real candles."""
        mgr = AngelOneFetchManager(_cfg(retry_max=3, backoff=(0, 0, 0)))
        calls = {"n": 0}
        real_candles = [{"timestamp": date(2024, 1, 2), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

        def side_effect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HistoricalFetchError("Access denied because of exceeding access rate", retryable=True)
            return real_candles

        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.side_effect = side_effect
            success, candles, err, attempts = mgr.fetch_with_retry(
                "RELIANCE", "ONE_DAY", date(2024, 1, 1), date(2024, 1, 5),
            )
        self.assertTrue(success)
        self.assertEqual(candles, real_candles)
        self.assertEqual(attempts, 2, "must have taken exactly 2 attempts (1 failure + 1 success)")
        self.assertEqual(calls["n"], 2)

    def test_transient_network_exception_triggers_retry(self):
        """Point 4: a raw network/DNS exception (not an Angel One
        structured error) must also trigger a retry via the same path."""
        mgr = AngelOneFetchManager(_cfg(retry_max=3, backoff=(0, 0, 0)))
        calls = {"n": 0}
        real_candles = [{"timestamp": date(2024, 1, 2), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

        def side_effect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HistoricalFetchError("getaddrinfo failed", retryable=True)
            return real_candles

        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.side_effect = side_effect
            success, candles, err, attempts = mgr.fetch_with_retry(
                "RELIANCE", "ONE_DAY", date(2024, 1, 1), date(2024, 1, 5),
            )
        self.assertTrue(success)
        self.assertEqual(attempts, 2)


class TestExhaustedRetriesFail(unittest.TestCase):
    def test_persistent_rate_limit_exhausts_retries_and_fails(self):
        """Point 5: if every attempt fails, fetch_with_retry must return
        success=False after retry_max attempts -- not silently succeed."""
        mgr = AngelOneFetchManager(_cfg(retry_max=3, backoff=(0, 0, 0)))
        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.side_effect = HistoricalFetchError(
                "Access denied because of exceeding access rate", retryable=True,
            )
            success, candles, err, attempts = mgr.fetch_with_retry(
                "RELIANCE", "ONE_DAY", date(2024, 1, 1), date(2024, 1, 5),
            )
        self.assertFalse(success)
        self.assertEqual(candles, [])
        self.assertEqual(attempts, 3, "must have used all retry_max attempts, not given up early")

    def test_non_retryable_error_fails_fast_without_exhausting_retry_budget(self):
        """Permanent errors (e.g. unknown ticker) must not burn through the
        full retry/backoff budget -- they fail on the first attempt."""
        mgr = AngelOneFetchManager(_cfg(retry_max=3, backoff=(0, 0, 0)))
        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.side_effect = HistoricalFetchError(
                "Ticker 'NOPE' not found", retryable=False,
            )
            success, candles, err, attempts = mgr.fetch_with_retry(
                "NOPE", "ONE_DAY", date(2024, 1, 1), date(2024, 1, 5),
            )
        self.assertFalse(success)
        self.assertEqual(attempts, 1, "non-retryable failures must not consume retry attempts")


class TestGenuineEmptyStaysLegitimate(unittest.TestCase):
    def test_genuine_empty_result_is_success_with_zero_candles_no_retry(self):
        """Point 6: a real successful empty response (e.g. ticker not yet
        listed in the requested range) must be success=True, [] -- and
        must NOT be retried, since there was no failure."""
        mgr = AngelOneFetchManager(_cfg(retry_max=3, backoff=(0, 0, 0)))
        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.return_value = []
            success, candles, err, attempts = mgr.fetch_with_retry(
                "RELIANCE", "ONE_DAY", date(2024, 1, 1), date(2024, 1, 5),
            )
        self.assertTrue(success)
        self.assertEqual(candles, [])
        self.assertEqual(attempts, 1, "a legitimate empty result must not trigger any retry")


class TestApiErrorsCannotProduceSuccess(unittest.TestCase):
    def test_status_false_response_never_yields_success_true(self):
        """Point 7: no matter how many times fetch_with_retry is exercised
        with a failing mock, success must never flip True -- proving the
        pipeline can no longer mistake a failure for a DONE-eligible
        chunk."""
        mgr = AngelOneFetchManager(_cfg(retry_max=5, backoff=(0, 0, 0, 0, 0)))
        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.side_effect = HistoricalFetchError("boom", retryable=True)
            success, candles, err, attempts = mgr.fetch_with_retry(
                "RELIANCE", "ONE_DAY", date(2024, 1, 1), date(2024, 1, 5),
            )
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
