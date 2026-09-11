"""
Regression tests for the root-cause bug found by the second controlled 1D
migration batch: historical_service.get_historical_candles() converted
EVERY failure (rate-limit rejection, auth error, network/DNS error,
malformed response) into an empty list `[]` -- indistinguishable from a
genuine, successful "no candles in this range" response. Downstream,
AngelOneFetchManager.fetch() only checked for `candles is None`, so an
empty list from ANY of those causes was treated as a successful chunk,
silently producing real historical gaps while every stat reported success.

These tests exercise get_historical_candles(raise_on_error=True) directly,
with a mocked smart_api (no real Angel One calls), proving the two cases
are now distinguishable and that legacy callers (raise_on_error=False,
the default) are completely unaffected.
"""
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from historical_service import HistoricalDataService, HistoricalFetchError


class _FreshService(HistoricalDataService):
    """HistoricalDataService is a singleton (__new__ returns the shared
    instance) -- tests need an independent instance so mocking smart_api on
    one test can't leak into another. Bypass the singleton by constructing
    via __new__.__wrapped__-style raw allocation instead of calling the
    class directly."""
    def __new__(cls):
        obj = object.__new__(cls)
        obj._initialized = False
        return obj


def _make_service(logged_in=True):
    svc = _FreshService()
    svc.api_key = "k"
    svc.client_id = "c"
    svc.password = "p"
    svc.totp_token = "t"
    svc.smart_api = MagicMock()
    svc.is_logged_in = logged_in
    svc._initialized = True
    return svc


class TestGenuineEmptyResultStaysLegitimate(unittest.TestCase):
    def test_status_true_empty_data_returns_empty_list_no_raise(self):
        """Case A from the task: API responds successfully and confirms
        zero candles for the range (e.g. before the ticker's listing date).
        This must NOT raise even with raise_on_error=True."""
        svc = _make_service()
        svc.smart_api.getCandleData.return_value = {"status": True, "data": []}
        with patch("angelone_service.angelone_service.get_token", return_value={"token": "1", "exchange": "NSE"}):
            candles = svc.get_historical_candles(
                "RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5), raise_on_error=True,
            )
        self.assertEqual(candles, [])


class TestApiFailuresAreNotSilentlyEmpty(unittest.TestCase):
    def test_rate_limit_response_raises_not_empty_list(self):
        """The exact defect: Angel One's 'Access denied because of
        exceeding access rate' comes back as status=False -- this must
        raise HistoricalFetchError(retryable=True), never return []."""
        svc = _make_service()
        svc.smart_api.getCandleData.return_value = {
            "status": False, "message": "Access denied because of exceeding access rate",
        }
        with patch("angelone_service.angelone_service.get_token", return_value={"token": "1", "exchange": "NSE"}):
            with self.assertRaises(HistoricalFetchError) as ctx:
                svc.get_historical_candles(
                    "RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5), raise_on_error=True,
                )
        self.assertTrue(ctx.exception.retryable)

    def test_network_exception_raises_retryable(self):
        """A DNS/timeout/connection error from smart_api.getCandleData
        itself (not a structured API response) must also raise, retryable
        -- these are exactly the transient failures observed live."""
        svc = _make_service()
        svc.smart_api.getCandleData.side_effect = ConnectionError("getaddrinfo failed")
        with patch("angelone_service.angelone_service.get_token", return_value={"token": "1", "exchange": "NSE"}):
            with self.assertRaises(HistoricalFetchError) as ctx:
                svc.get_historical_candles(
                    "RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5), raise_on_error=True,
                )
        self.assertTrue(ctx.exception.retryable)

    def test_malformed_response_raises_retryable(self):
        """JSON-parse-style failures ('Access denied...' wrapped as a
        parse error by the SDK) must also raise, not silently empty out."""
        svc = _make_service()
        svc.smart_api.getCandleData.side_effect = ValueError(
            "Couldn't parse the JSON response received from the server: b'Access denied because of exceeding access rate'"
        )
        with patch("angelone_service.angelone_service.get_token", return_value={"token": "1", "exchange": "NSE"}):
            with self.assertRaises(HistoricalFetchError):
                svc.get_historical_candles(
                    "RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5), raise_on_error=True,
                )

    def test_unknown_ticker_raises_not_retryable(self):
        """A permanent, config-level failure (token lookup fails) should be
        marked non-retryable so the retry loop doesn't waste attempts on
        something that will fail identically every time."""
        svc = _make_service()
        with patch("angelone_service.angelone_service.get_token", return_value=None):
            with self.assertRaises(HistoricalFetchError) as ctx:
                svc.get_historical_candles(
                    "NOPE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5), raise_on_error=True,
                )
        self.assertFalse(ctx.exception.retryable)

    def test_not_logged_in_raises_retryable(self):
        svc = _make_service(logged_in=False)
        with self.assertRaises(HistoricalFetchError) as ctx:
            svc.get_historical_candles(
                "RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5), raise_on_error=True,
            )
        self.assertTrue(ctx.exception.retryable)


class TestLegacyCallersUnaffected(unittest.TestCase):
    """raise_on_error defaults to False -- every one of the ~30 other
    call sites in the codebase (main.py, recovery_service.py, one-off
    scripts) must see exactly today's behavior: always a list, never an
    exception, regardless of what failed."""

    def test_default_still_returns_empty_list_on_rate_limit(self):
        svc = _make_service()
        svc.smart_api.getCandleData.return_value = {
            "status": False, "message": "Access denied because of exceeding access rate",
        }
        with patch("angelone_service.angelone_service.get_token", return_value={"token": "1", "exchange": "NSE"}):
            candles = svc.get_historical_candles("RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5))
        self.assertEqual(candles, [])

    def test_default_still_returns_empty_list_on_network_error(self):
        svc = _make_service()
        svc.smart_api.getCandleData.side_effect = ConnectionError("boom")
        with patch("angelone_service.angelone_service.get_token", return_value={"token": "1", "exchange": "NSE"}):
            candles = svc.get_historical_candles("RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5))
        self.assertEqual(candles, [])

    def test_default_still_returns_empty_list_when_not_logged_in(self):
        svc = _make_service(logged_in=False)
        candles = svc.get_historical_candles("RELIANCE", "ONE_DAY", date(2020, 1, 1), date(2020, 1, 5))
        self.assertEqual(candles, [])


if __name__ == "__main__":
    unittest.main()
