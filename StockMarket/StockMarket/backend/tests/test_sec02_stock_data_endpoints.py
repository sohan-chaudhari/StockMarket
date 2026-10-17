"""SEC-02 regression: heavy /api/stock-data/* endpoints.

The six legacy stock-data endpoints were public, unauthenticated, un-rate-limited
and unbounded on the provider side. Confirmed findings (see the SEC-02 report):

  * For a ticker whose stored history is short or stale, the "need fill" test in
    /api/stock-data/{range,weekly,monthly} stays true on EVERY request, so each
    anonymous visit could re-download a full `period='max'` history from
    yfinance and write candles -- and the per-ticker _backfill_cooldown registry
    that guards the intraday path was not consulted by any of them.
  * None of the six had a rate limit (the only @limiter.limit in main.py was
    /api/live-prices) or any bound on the `ticker` input.

The controls under test (all reuse existing infrastructure -- no new service,
cache, thread or schema):
  * every one of the six routes is now rate-limited through the shared slowapi
    limiter (so a single client cannot hammer them);
  * the 1D/1W/1M provider gap-fill is throttled per ticker via the SAME
    _backfill_cooldown registry the intraday path uses -- a repeat request for
    the same ticker within the window is served from stored candles with NO
    provider call and NO database write;
  * `ticker`/`range`/`timeframe` inputs are length-bounded;
  * public (anonymous) read access is preserved -- login is NOT required.

Hermetic: no real provider is ever called (yfinance/AngelOne are mocked) and no
production/Postgres is touched (in-memory SQLite or a recording fake session).
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-auth-tests-only")

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from database import Base, get_db


# --------------------------------------------------------------------------
# Public routes under test + a helper for the slowapi registration key format
# (slowapi stores limits under f"{func.__module__}.{func.__name__}").
# --------------------------------------------------------------------------
PUBLIC_ROUTES = [
    "get_stock_data_range",
    "get_stock_data_intraday",
    "get_weekly_intraday_candles_5min",
    "get_monthly_intraday_candles_5min",
    "get_history_before",
    "get_chart_service",
]


def _limit_key(fn) -> str:
    return f"{fn.__module__}.{fn.__name__}"


def _mk_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _Query:
    """Chainable no-op query used by the recording fake session."""
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def all(self): return []
    def first(self): return None


class _RecordingSession:
    """Minimal Session stand-in that counts provider-driven writes.

    Used instead of a real SQLite session so the PostgreSQL upsert() objects the
    routes build can be counted without executing (they are dialect-specific)."""
    def __init__(self):
        self.execute_calls = 0
        self.commit_calls = 0

    def query(self, *a, **k): return _Query()
    def execute(self, *a, **k): self.execute_calls += 1
    def commit(self): self.commit_calls += 1
    def rollback(self): pass


def _daily_df():
    """One row of daily OHLCV (enough to exercise the /range write path)."""
    idx = pd.to_datetime(["2024-01-15"])
    return pd.DataFrame(
        {"Open": [100.0], "High": [110.0], "Low": [95.0], "Close": [105.0], "Volume": [1000]},
        index=idx,
    )


# ==========================================================================
# 1. Rate-limit wiring: every one of the six routes is throttled.
# ==========================================================================
class RateLimitWiringTests(unittest.TestCase):
    def test_all_six_routes_are_rate_limited(self):
        for name in PUBLIC_ROUTES:
            fn = getattr(main, name)
            key = _limit_key(fn)
            self.assertIn(
                key, main.limiter._route_limits,
                f"{name} must be registered with the shared slowapi limiter",
            )
            self.assertTrue(main.limiter._route_limits[key],
                            f"{name} must have at least one rate limit")


# ==========================================================================
# 2. Enforcement over HTTP on a minimal app that mounts the REAL decorated
#    route functions (the limiter is the shared main.limiter instance).
# ==========================================================================
def _mini_app():
    mini = FastAPI()
    mini.state.limiter = main.limiter

    @mini.exception_handler(RateLimitExceeded)
    async def _handler(request, exc):
        return JSONResponse(status_code=429,
                            content={"detail": "Too many requests. Please try again later."})

    def _override_db():
        db = _mk_db()
        try:
            yield db
        finally:
            db.close()

    mini.dependency_overrides[get_db] = _override_db
    mini.get("/chart")(main.get_chart_service)
    mini.get("/range")(main.get_stock_data_range)
    mini.get("/weekly")(main.get_weekly_intraday_candles_5min)
    return mini


class RateLimitEnforcementTests(unittest.TestCase):
    def setUp(self):
        main.limiter.reset()

    def test_chart_endpoint_eventually_returns_429(self):
        client = TestClient(_mini_app())
        statuses = set()
        for _ in range(245):  # limit is 240/minute
            statuses.add(client.get("/chart", params={"ticker": "ZZSEC02RL"}).status_code)
        self.assertIn(200, statuses)
        self.assertIn(429, statuses, "the endpoint must start rate-limiting a hammering client")

    def test_oversized_ticker_is_rejected_with_422(self):
        client = TestClient(_mini_app())
        resp = client.get("/chart", params={"ticker": "A" * 41})
        self.assertEqual(resp.status_code, 422)
        # A valid-length ticker is still accepted.
        self.assertNotEqual(client.get("/chart", params={"ticker": "RELIANCE"}).status_code, 422)


# ==========================================================================
# 3. Public (anonymous) access is preserved -- login is NOT required.
# ==========================================================================
class AnonymousAccessPreservedTests(unittest.TestCase):
    def setUp(self):
        main.limiter.reset()

    def test_chart_is_publicly_readable(self):
        # No cookie, no Authorization header.
        resp = TestClient(_mini_app()).get("/chart", params={"ticker": "ZZSEC02PUB"})
        self.assertEqual(resp.status_code, 200)

    def test_range_is_publicly_readable(self):
        with mock.patch.object(main.yf_downloader, "download_single", return_value=(None, "err")), \
             mock.patch.object(main, "historical_service", mock.MagicMock(is_logged_in=False)):
            resp = TestClient(_mini_app()).get("/range", params={"ticker": "ZZSEC02PUB"})
        self.assertEqual(resp.status_code, 200)

    def test_weekly_is_publicly_readable(self):
        with mock.patch.object(main.yf, "download", return_value=pd.DataFrame()):
            resp = TestClient(_mini_app()).get("/weekly", params={"ticker": "ZZSEC02PUB"})
        self.assertEqual(resp.status_code, 200)


# ==========================================================================
# 4. Provider gap-fill is throttled per ticker -- no repeat provider call and
#    no repeat DB write while the cooldown is active.
# ==========================================================================
class DailyRangeCooldownTests(unittest.TestCase):
    def setUp(self):
        main._backfill_cooldown.clear()

    def tearDown(self):
        main._backfill_cooldown.clear()

    def test_range_provider_fetch_is_throttled(self):
        db = _RecordingSession()
        calls = {"n": 0}

        def _fake_single(*a, **k):
            calls["n"] += 1
            return _daily_df(), None

        key = "ZZSEC02DC:daily_gapfill"
        with mock.patch.object(main.yf_downloader, "download_single", side_effect=_fake_single), \
             mock.patch.object(main, "historical_service", mock.MagicMock(is_logged_in=False)):
            main._stock_data_range_core("ZZSEC02DC", "ALL", db)
            first_calls, first_execs, first_commits = calls["n"], db.execute_calls, db.commit_calls
            self.assertTrue(main._backfill_cooldown_active(key), "a fill attempt must set the cooldown")
            self.assertGreater(first_execs, 0, "the first fill writes candles")

            # Second request for the SAME ticker, inside the cooldown window.
            main._stock_data_range_core("ZZSEC02DC", "ALL", db)

        self.assertEqual(calls["n"], first_calls, "no repeat provider call while throttled")
        self.assertEqual(db.execute_calls, first_execs, "no repeat DB write while throttled")
        self.assertEqual(db.commit_calls, first_commits, "no repeat commit while throttled")

    def test_range_fetch_resumes_after_cooldown_expires(self):
        db = _RecordingSession()
        calls = {"n": 0}

        def _fake_single(*a, **k):
            calls["n"] += 1
            return None, "err"

        with mock.patch.object(main.yf_downloader, "download_single", side_effect=_fake_single), \
             mock.patch.object(main, "historical_service", mock.MagicMock(is_logged_in=False)):
            main._stock_data_range_core("ZZSEC02DX", "ALL", db)
            self.assertEqual(calls["n"], 1)
            main._set_backfill_cooldown("ZZSEC02DX:daily_gapfill", -1)  # force expiry
            main._stock_data_range_core("ZZSEC02DX", "ALL", db)
        self.assertEqual(calls["n"], 2, "an expired cooldown must allow a new fill attempt")


class ResampleGapFillCooldownTests(unittest.TestCase):
    def setUp(self):
        main._backfill_cooldown.clear()

    def tearDown(self):
        main._backfill_cooldown.clear()

    def test_weekly_gap_fill_is_throttled(self):
        db = _mk_db()
        calls = {"n": 0}

        def _fake_download(*a, **k):
            calls["n"] += 1
            return pd.DataFrame()  # provider WAS called; no rows to store

        key = "ZZSEC02WC:resample1W"
        with mock.patch.object(main.yf, "download", side_effect=_fake_download):
            main._resample_gap_fill("ZZSEC02WC", "1W", [], ["ZZSEC02WC"], db)
            self.assertEqual(calls["n"], 1)
            self.assertTrue(main._backfill_cooldown_active(key))
            main._resample_gap_fill("ZZSEC02WC", "1W", [], ["ZZSEC02WC"], db)
        self.assertEqual(calls["n"], 1, "a repeat 1W gap-fill must be throttled")

    def test_monthly_gap_fill_is_throttled(self):
        db = _mk_db()
        calls = {"n": 0}

        def _fake_download(*a, **k):
            calls["n"] += 1
            return pd.DataFrame()

        key = "ZZSEC02MC:resample1M"
        with mock.patch.object(main.yf, "download", side_effect=_fake_download):
            main._resample_gap_fill("ZZSEC02MC", "1M", [], ["ZZSEC02MC"], db)
            self.assertEqual(calls["n"], 1)
            self.assertTrue(main._backfill_cooldown_active(key))
            main._resample_gap_fill("ZZSEC02MC", "1M", [], ["ZZSEC02MC"], db)
        self.assertEqual(calls["n"], 1, "a repeat 1M gap-fill must be throttled")

    def test_resample_cooldown_key_does_not_collide_with_intraday(self):
        # The intraday path uses f"{ticker}:{interval}"; SEC-02 keys must differ.
        main._set_backfill_cooldown("ZZSEC02COL:5m", 60)
        self.assertFalse(main._backfill_cooldown_active("ZZSEC02COL:resample1W"))
        self.assertFalse(main._backfill_cooldown_active("ZZSEC02COL:daily_gapfill"))


# ==========================================================================
# 5. history-before reuses the same bounded core helpers (no double-count and
#    no provider call when its timeframe branch is unknown).
# ==========================================================================
class HistoryBeforeTests(unittest.TestCase):
    def setUp(self):
        main.limiter.reset()
        main._backfill_cooldown.clear()

    def tearDown(self):
        main._backfill_cooldown.clear()

    def _client(self):
        mini = _mini_app()
        mini.get("/history-before")(main.get_history_before)
        return TestClient(mini)

    def test_unknown_timeframe_makes_no_provider_call(self):
        calls = {"n": 0}

        def _fake_download(*a, **k):
            calls["n"] += 1
            return pd.DataFrame()

        with mock.patch.object(main.yf, "download", side_effect=_fake_download):
            resp = self._client().get(
                "/history-before", params={"ticker": "ZZSEC02HB", "timeframe": "4h"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"data": [], "has_more": False})
        self.assertEqual(calls["n"], 0, "an unrecognised timeframe must not reach a provider")

    def test_weekly_branch_works_anonymously(self):
        # If this route re-entered the *decorated* weekly route instead of the
        # *_core helper, slowapi would reject the missing Request and return 500.
        with mock.patch.object(main.yf, "download", return_value=pd.DataFrame()):
            resp = self._client().get(
                "/history-before", params={"ticker": "ZZSEC02HB2", "timeframe": "1W"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("data", resp.json())

    def test_ticker_is_length_bounded(self):
        resp = self._client().get(
            "/history-before", params={"ticker": "A" * 41, "timeframe": "1W"})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
