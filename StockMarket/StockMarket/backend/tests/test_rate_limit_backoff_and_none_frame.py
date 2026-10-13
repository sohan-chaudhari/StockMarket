"""Regression tests for the two defects behind "09:15/09:20 candles still missing".

1. historical_service: Angel One throttles the historical REST API ("Access denied
   because of exceeding access rate"). Nothing backed off globally, so the retry
   loops (intraday prefill attempts 1/2/3, dashboard index backfill) kept hammering
   while throttled -- holding the window open and starving the ON-DEMAND chart
   fills, so a missing opening bar could never be fetched. Now one throttle pauses
   every historical call for a cooldown.

2. recovery_service: `download_single` returns (Optional[DataFrame], Optional[str]),
   so the frame can be None; `if df and not df[0].empty` therefore raised
   AttributeError on every attempt and the yfinance recovery fallback could never
   contribute a candle.
"""
import os
import re
import time
import unittest

import historical_service as hs

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECOVERY_SRC = os.path.join(BACKEND_DIR, "recovery_service.py")


class HistoricalRateLimitBackoffTests(unittest.TestCase):
    def setUp(self):
        self._saved = hs._rate_limit_until
        hs._rate_limit_until = 0.0
        self.addCleanup(self._restore)
        self.svc = hs.HistoricalDataService()
        self.svc.is_logged_in = True

    def _restore(self):
        hs._rate_limit_until = self._saved

    def test_no_backoff_when_clear(self):
        self.assertEqual(hs.rate_limit_backoff_remaining(), 0.0)

    def test_a_throttle_starts_a_global_backoff(self):
        hs._note_rate_limit()
        self.assertGreater(hs.rate_limit_backoff_remaining(), 0.0)
        self.assertLessEqual(hs.rate_limit_backoff_remaining(), hs._RATE_LIMIT_BACKOFF_SEC + 1)

    def test_calls_short_circuit_while_backed_off(self):
        hs._note_rate_limit()
        # No API/token lookup may happen -- the call must return immediately.
        self.assertEqual(
            self.svc.get_historical_candles("HDFCLIFE", "FIVE_MINUTE", None, None), [])

    def test_raise_on_error_callers_get_a_retryable_error(self):
        hs._note_rate_limit()
        with self.assertRaises(hs.HistoricalFetchError) as ctx:
            self.svc.get_historical_candles("HDFCLIFE", "FIVE_MINUTE", None, None,
                                            raise_on_error=True)
        self.assertTrue(ctx.exception.retryable)

    def test_backoff_expires(self):
        hs._note_rate_limit()
        hs._rate_limit_until = time.time() - 1
        self.assertEqual(hs.rate_limit_backoff_remaining(), 0.0)

    def test_non_json_throttle_body_also_engages_the_backoff(self):
        # Angel One signals a throttle as a NON-JSON body, so it surfaces as a
        # JSON-parse exception rather than a status=False response. Detecting only
        # the latter is why the backoff never fired while the app was being
        # throttled ~80 times per 30 minutes.
        self.assertTrue(hs._looks_rate_limited(
            "Couldn't parse the JSON response received from the server: "
            "b'Access denied because of exceeding access rate'"))
        self.assertFalse(hs._looks_rate_limited("Invalid or unsupported symbol"))


class RecoveryYfinanceNoneFrameTests(unittest.TestCase):
    """The yfinance recovery fallback must tolerate a None frame."""

    def test_guard_exists_in_source(self):
        with open(RECOVERY_SRC, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotRegex(src, r"if\s+df\s+and\s+not\s+df\[0\]\.empty",
                            "unguarded df[0].empty (crashes on a None frame)")
        self.assertRegex(src, r"df\[0\]\s+is\s+not\s+None\s+and\s+not\s+df\[0\]\.empty")

    def test_download_single_can_return_a_none_frame(self):
        # Documents the contract the guard exists for.
        from yfinance_downloader import YFinanceDownloader
        sig = YFinanceDownloader.download_single.__doc__ or ""
        self.assertIn("None", sig)


if __name__ == "__main__":
    unittest.main()
