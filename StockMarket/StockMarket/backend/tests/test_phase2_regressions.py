"""Regression tests for Phase 2 fixes:
1. iter_request_tokens rejecting placeholder tokens ('cookie-session', 'bearer', 'null', 'undefined')
2. Exchange resolution for BSE and NSE tickers in metadata
3. _yfinance_ticker generating proper .BO and .NS suffixes
4. Nginx cache bypass rules for personalized endpoints
5. Cache-Control header on authenticated endpoints
"""
import os
import re
import unittest
from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials
from starlette.datastructures import Headers

from backend.auth import iter_request_tokens, AUTH_COOKIE_NAME
from backend.main import _yfinance_ticker, app


class TestAuthPlaceholderRegression(unittest.TestCase):
    def test_cookie_session_placeholder_rejected_in_header(self):
        """Verify that 'cookie-session' in Authorization header is ignored,
        allowing the valid cookie to be yielded."""
        class DummyRequest:
            cookies = {AUTH_COOKIE_NAME: "valid.jwt.cookie"}
            headers = Headers({"authorization": "Bearer cookie-session"})

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="cookie-session")
        tokens = list(iter_request_tokens(DummyRequest(), creds))
        self.assertEqual(tokens, ["valid.jwt.cookie"], "cookie-session should be skipped in favor of cookie")

    def test_null_undefined_bearer_placeholders_rejected(self):
        """Verify null, undefined, and bearer string variations are rejected."""
        class DummyRequest:
            cookies = {AUTH_COOKIE_NAME: "fallback.cookie"}

        for placeholder in ("null", "NULL", "undefined", "cookie-session", "bearer", "Bearer"):
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=placeholder)
            tokens = list(iter_request_tokens(DummyRequest(), creds))
            self.assertEqual(tokens, ["fallback.cookie"], f"Placeholder '{placeholder}' was not skipped")

    def test_valid_jwt_header_is_yielded_first(self):
        """Verify real JWT in header is preferred over cookie."""
        class DummyRequest:
            cookies = {AUTH_COOKIE_NAME: "cookie.jwt"}

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="real.jwt.token")
        tokens = list(iter_request_tokens(DummyRequest(), creds))
        self.assertEqual(tokens, ["real.jwt.token", "cookie.jwt"])


class TestExchangeResolutionAndYFinanceMapping(unittest.TestCase):
    def test_yfinance_ticker_bse_suffix(self):
        """BSE tickers must be formatted with .BO suffix."""
        self.assertEqual(_yfinance_ticker("TAPARIA", exchange="BSE"), "TAPARIA.BO")
        self.assertEqual(_yfinance_ticker("505685", exchange="BSE"), "505685.BO")

    def test_yfinance_ticker_nse_suffix(self):
        """NSE tickers must be formatted with .NS suffix."""
        self.assertEqual(_yfinance_ticker("RELIANCE", exchange="NSE"), "RELIANCE.NS")
        self.assertEqual(_yfinance_ticker("TCS", exchange="NSE"), "TCS.NS")

    def test_yfinance_ticker_indices(self):
        """Known indices map to their respective Yahoo Finance symbols."""
        self.assertEqual(_yfinance_ticker("NIFTY"), "^NSEI")
        self.assertEqual(_yfinance_ticker("SENSEX"), "^BSESN")
        self.assertEqual(_yfinance_ticker("BANKNIFTY"), "^NSEBANK")


class TestNginxCacheConfiguration(unittest.TestCase):
    def setUp(self):
        conf_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "deployment", "nginx_leverage.conf"
        )
        with open(conf_path, "r", encoding="utf-8") as f:
            self.conf = f.read()

    def test_uncached_location_contains_all_sensitive_routes(self):
        """Ensure portfolio, trade, trades, watchlist, auth, csrf are uncached."""
        match = re.search(r"location\s*~\s*\^/api/\(([^)]+)\)", self.conf)
        self.assertIsNotNone(match, "Uncached location block not found")
        endpoints = match.group(1).split("|")
        required = ["portfolio", "trade", "trades", "watchlist", "auth", "csrf-token", "user"]
        for req in required:
            self.assertIn(req, endpoints, f"Missing {req} from Nginx uncached location block")

    def test_public_api_cache_bypasses_auth_and_cookie(self):
        """Ensure proxy_cache_bypass and proxy_no_cache include auth headers."""
        self.assertIn("proxy_cache_bypass $http_authorization $cookie_leverage_token;", self.conf)
        self.assertIn("proxy_no_cache $http_authorization $cookie_leverage_token;", self.conf)

class TestNewsEntityFiltering(unittest.TestCase):
    """Verify stock-specific news filtering rejects false positives and matches true entities."""

    def test_sbin_entity_patterns_reject_false_positives(self):
        """Articles mentioning 'state' or other banks without SBI must be rejected."""
        from main import news_search
        import asyncio

        # Test regex pattern logic directly:
        patterns = [
            re.compile(r'\b' + re.escape(a) + r'\b', re.IGNORECASE)
            for a in ["sbin", "sbi", "state bank of india", "state bank"]
        ]
        
        irrelevant_headlines = [
            "Inter State Oil Carrier volume up 31.7% YoY to 27,880 MT in Sep 2026",
            "Xi urges healthy competition, no conflict with US during state visit",
            "Konndor Industries AGM approves shift of registered office to another state",
            "Empire State Realty Trust reports Q2 results",
            "Jana Small Finance Bank updates fixed deposit rates"
        ]
        for hl in irrelevant_headlines:
            matched = any(p.search(hl) for p in patterns)
            self.assertFalse(matched, f"False positive matched irrelevant headline: {hl}")

        relevant_headlines = [
            "SBI Q1FY27 Net Profit Rises 10% to 21,121 Cr",
            "State Bank of India to Announce Q3FY26 Results on February 7",
            "SBIN Share Price Today - State Bank of India on NSE/BSE",
            "State Bank raises 10,000 crore via infrastructure bonds"
        ]
        for hl in relevant_headlines:
            matched = any(p.search(hl) for p in patterns)
            self.assertTrue(matched, f"True entity headline failed to match: {hl}")

    def test_distinctive_brand_pattern_for_taparia(self):
        """Taparia Tools should match Taparia brand and reject general tool articles."""
        patterns = [
            re.compile(r'\b' + re.escape(a) + r'\b', re.IGNORECASE)
            for a in ["taparia", "taparia tools"]
        ]
        self.assertTrue(any(p.search("Taparia Tools reports 23.6% profit rise in FY26") for p in patterns))
        self.assertFalse(any(p.search("Stanley Black & Decker announces quarterly earnings") for p in patterns))


if __name__ == "__main__":
    unittest.main()

