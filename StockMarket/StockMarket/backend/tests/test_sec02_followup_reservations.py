"""SEC-02 follow-up regression: atomic per-ticker reservations + a process-wide
on-demand provider-fill budget + rate limits on the four remaining routes.

Confirmed problems this guards (see the SEC-02 follow-up report):
  * the per-ticker cooldown was a check-then-act (TOCTOU) -- two concurrent
    requests for the same cold ticker both started the provider fill;
  * there was no AGGREGATE limit on on-demand provider work, so one client
    enumerating distinct tickers could sustain heavy yfinance downloads;
  * four stock-data routes (intraday/paginated, range/paginated, intraday/since,
    candle/latest) had no rate limit at all.

Hermetic: providers are mocked, the DB is an in-memory SQLite or a recording
fake, and time is driven by an INJECTED clock -- no sleeps, no real provider,
no Postgres.
"""
import os
import threading
import unittest
from datetime import datetime, timedelta
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
import models
from database import Base, get_db


# --------------------------------------------------------------------------
# Deterministic time + lightweight fakes
# --------------------------------------------------------------------------
class _Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t
    def __call__(self):
        return self.t
    def advance(self, d):
        self.t += d


class _Query:
    def __init__(self, rows=None):
        self._rows = rows if rows is not None else []
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def all(self): return self._rows
    def first(self): return self._rows[0] if self._rows else None


class _FakeSession:
    """Recording session: returns empty reads, counts provider-driven writes."""
    def __init__(self, candle_rows=None):
        self.execute_calls = 0
        self.commit_calls = 0
        self._rows = candle_rows or []
    def query(self, model, *a, **k):
        if model is models.Candle:
            return _Query(self._rows)
        return _Query([])
    def execute(self, *a, **k): self.execute_calls += 1
    def commit(self): self.commit_calls += 1
    def rollback(self): pass


class _Row:
    def __init__(self, ts):
        self.timestamp = ts
        self.open = 100.0
        self.high = 101.0
        self.low = 99.0
        self.close = 100.5
        self.volume = 10


def _mk_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _one_row_df():
    idx = pd.to_datetime(["2024-01-15"])
    return pd.DataFrame(
        {"Open": [100.0], "High": [110.0], "Low": [95.0], "Close": [105.0], "Volume": [1000]},
        index=idx,
    )


def _reset_state():
    main._backfill_cooldown.clear()
    with main._ondemand_budget_lock:
        main._ondemand_budget["window_start"] = 0.0
        main._ondemand_budget["used"] = 0


def _reset_budget():
    with main._ondemand_budget_lock:
        main._ondemand_budget["window_start"] = 0.0
        main._ondemand_budget["used"] = 0


class _Isolated(unittest.TestCase):
    """Injected clock + clean cooldown/budget state for every test."""
    def setUp(self):
        self.clock = _Clock()
        self._orig_clock = main._backfill_clock
        main._backfill_clock = self.clock
        _reset_state()

    def tearDown(self):
        main._backfill_clock = self._orig_clock
        _reset_state()


# ==========================================================================
# 1. Atomic reservation -- concurrency
# ==========================================================================
class AtomicReservationTests(_Isolated):
    def _run_range(self, ticker, fake_single, capture=None):
        db = _FakeSession()
        with mock.patch.object(main.yf_downloader, "download_single", side_effect=fake_single), \
             mock.patch.object(main, "historical_service", mock.MagicMock(is_logged_in=False)):
            out = main._stock_data_range_core(ticker, "ALL", db)
        if capture is not None:
            capture.append(out)
        return out

    def test_concurrent_same_ticker_starts_only_one_fill(self):
        calls = {"n": 0}
        entered = threading.Event()
        release = threading.Event()

        def fake_single(*a, **k):
            calls["n"] += 1
            entered.set()
            release.wait(timeout=10)
            return None, "err"

        t1 = threading.Thread(target=self._run_range, args=("ZZCONC", fake_single))
        t1.start()
        self.assertTrue(entered.wait(timeout=10), "first request never reached the provider")

        # While thread 1 holds the reservation, a concurrent identical request
        # must NOT start a second fill.
        t2 = threading.Thread(target=self._run_range, args=("ZZCONC", fake_single))
        t2.start()
        t2.join(timeout=10)
        self.assertFalse(t2.is_alive())

        release.set()
        t1.join(timeout=10)
        self.assertEqual(calls["n"], 1, "two concurrent requests must produce exactly one fill")

    def test_abandoned_reservation_expires_and_does_not_block(self):
        key = "ZZABANDON:resample1W"
        self.assertTrue(main._try_reserve_backfill(key))
        # Never finalised -- simulates a crash mid-fill.
        self.assertFalse(main._try_reserve_backfill(key))
        self.clock.advance(main._BACKFILL_INFLIGHT_SEC + 1)
        self.assertTrue(main._try_reserve_backfill(key),
                        "an expired reservation must not block the ticker forever")


# ==========================================================================
# 2/3/4/5. Process-wide budget
# ==========================================================================
class OnDemandBudgetTests(_Isolated):
    def _run_range(self, ticker, fake_single):
        db = _FakeSession()
        with mock.patch.object(main.yf_downloader, "download_single", side_effect=fake_single), \
             mock.patch.object(main, "historical_service", mock.MagicMock(is_logged_in=False)):
            return main._stock_data_range_core(ticker, "ALL", db)

    def test_distinct_tickers_respect_process_wide_budget(self):
        orig = main._ONDEMAND_FILL_BUDGET_PER_MIN
        main._ONDEMAND_FILL_BUDGET_PER_MIN = 2
        try:
            calls = {"n": 0}

            def fake_single(*a, **k):
                calls["n"] += 1
                return None, "err"

            for tk in ("ZZB1", "ZZB2", "ZZB3"):
                self._run_range(tk, fake_single)
            self.assertEqual(calls["n"], 2,
                             "only the budgeted number of distinct-ticker fills may start")
        finally:
            main._ONDEMAND_FILL_BUDGET_PER_MIN = orig

    def test_stored_read_does_not_consume_budget_or_reserve(self):
        today = main.database.get_ist_now().date()
        rows = [_Row(datetime.combine(today - timedelta(days=i), datetime.min.time()))
                for i in range(12)]
        db = _FakeSession(candle_rows=rows)
        with mock.patch.object(main, "_try_reserve_backfill") as res_mock, \
             mock.patch.object(main, "_try_consume_ondemand_budget") as bud_mock:
            out = main._stock_data_range_core("ZZSTORED", "ALL", db)
        self.assertTrue(out, "stored candles must still be returned")
        self.assertFalse(res_mock.called, "a stored-only read must not reserve a fill")
        self.assertFalse(bud_mock.called, "a stored-only read must not consume budget")

    def test_weekly_history_fill_charges_the_budget_once(self):
        before = main._ondemand_budget["used"]
        with mock.patch.object(main.yf, "download", return_value=pd.DataFrame()):
            main._weekly_candles_core("ZZDEL", _mk_sqlite())
        self.assertEqual(main._ondemand_budget["used"] - before, 1,
                         "one logical history fill must charge the budget exactly once")

    def test_budget_exhaustion_serves_stored_data_and_fabricates_nothing(self):
        orig = main._ONDEMAND_FILL_BUDGET_PER_MIN
        main._ONDEMAND_FILL_BUDGET_PER_MIN = 1
        try:
            self.assertTrue(main._try_consume_ondemand_budget())  # exhaust the window
            calls = {"n": 0}

            def fake_single(*a, **k):
                calls["n"] += 1
                return _one_row_df(), None

            out = self._run_range("ZZBUD", fake_single)
            self.assertEqual(calls["n"], 0, "no provider call once the budget is exhausted")
            self.assertEqual(out, [], "an exhausted budget must return stored data (none here)")
            self.assertFalse(main._backfill_cooldown_active("ZZBUD:daily_gapfill"),
                             "the reservation must be released when the budget is unavailable")
        finally:
            main._ONDEMAND_FILL_BUDGET_PER_MIN = orig

    def test_budget_window_resets(self):
        orig = main._ONDEMAND_FILL_BUDGET_PER_MIN
        main._ONDEMAND_FILL_BUDGET_PER_MIN = 1
        try:
            self.assertTrue(main._try_consume_ondemand_budget())
            self.assertFalse(main._try_consume_ondemand_budget())
            self.clock.advance(61)
            self.assertTrue(main._try_consume_ondemand_budget(), "a new window must reset the budget")
        finally:
            main._ONDEMAND_FILL_BUDGET_PER_MIN = orig


# ==========================================================================
# 6/7. Reservation outcome handling
# ==========================================================================
class ReservationOutcomeTests(_Isolated):
    def _weekly_fill(self, ticker, fake_download):
        with mock.patch.object(main.yf, "download", side_effect=fake_download):
            main._resample_gap_fill(ticker, "1W", [], [ticker], _mk_sqlite())

    def _remaining(self, key):
        return main._backfill_cooldown.get(key, 0.0) - self.clock.t

    def test_success_sets_the_success_cooldown(self):
        self._weekly_fill("ZZOK", lambda *a, **k: _one_row_df())
        self.assertAlmostEqual(self._remaining("ZZOK:resample1W"),
                               main._DAILY_HISTORY_FILL_COOLDOWN_SEC, places=3)

    def test_empty_result_gets_only_the_short_backoff(self):
        self._weekly_fill("ZZEMPTY", lambda *a, **k: pd.DataFrame())
        self.assertAlmostEqual(self._remaining("ZZEMPTY:resample1W"),
                               main._BACKFILL_FAIL_MIN_SEC, places=3)

    def test_exception_gets_only_the_short_backoff(self):
        def _boom(*a, **k):
            raise RuntimeError("provider down")
        self._weekly_fill("ZZERR", _boom)
        self.assertAlmostEqual(self._remaining("ZZERR:resample1W"),
                               main._BACKFILL_FAIL_MIN_SEC, places=3)

    def test_failed_fill_recovers_after_the_backoff(self):
        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            raise RuntimeError("down")

        self._weekly_fill("ZZREC", _boom)
        self.assertEqual(calls["n"], 1)
        self._weekly_fill("ZZREC", _boom)          # inside the backoff
        self.assertEqual(calls["n"], 1, "a transient failure must not be retried immediately")
        self.clock.advance(main._BACKFILL_FAIL_MIN_SEC + 1)
        self._weekly_fill("ZZREC", _boom)          # after the backoff
        self.assertEqual(calls["n"], 2, "the fill must recover once the backoff expires")


# ==========================================================================
# 9. The four newly rate-limited routes
# ==========================================================================
def _mini_app():
    mini = FastAPI()
    mini.state.limiter = main.limiter

    @mini.exception_handler(RateLimitExceeded)
    async def _handler(request, exc):
        return JSONResponse(status_code=429,
                            content={"detail": "Too many requests. Please try again later."})

    def _override_db():
        db = _mk_sqlite()
        try:
            yield db
        finally:
            db.close()

    mini.dependency_overrides[get_db] = _override_db
    mini.get("/candle/latest")(main.get_latest_candle)
    mini.get("/range/paginated")(main.get_range_paginated)
    mini.get("/intraday/since")(main.get_intraday_since)
    mini.get("/intraday/paginated")(main._intraday_paginated_route)
    return mini


NEW_ROUTE_FUNCS = ("_intraday_paginated_route", "get_range_paginated",
                   "get_intraday_since", "get_latest_candle")


class NewRouteRateLimitTests(unittest.TestCase):
    def setUp(self):
        main.limiter.reset()
        _reset_state()

    def tearDown(self):
        main.limiter.reset()
        _reset_state()

    def test_all_four_new_routes_are_registered_with_limits(self):
        for name in NEW_ROUTE_FUNCS:
            fn = getattr(main, name)
            key = f"{fn.__module__}.{fn.__name__}"
            self.assertIn(key, main.limiter._route_limits, f"{name} must be rate-limited")
            self.assertTrue(main.limiter._route_limits[key], f"{name} must have a limit")

    def test_each_new_route_returns_429_under_hammering(self):
        # (path, params, max requests for that route's limit)
        cases = [
            ("/candle/latest", {"ticker": "ZZRL"}, 245),
            ("/range/paginated", {"ticker": "ZZRL"}, 125),
            ("/intraday/since", {"ticker": "ZZRL", "since": 1}, 245),
            ("/intraday/paginated", {"ticker": "ZZRL"}, 125),
        ]
        for path, params, n in cases:
            with self.subTest(path=path):
                main.limiter.reset()
                client = TestClient(_mini_app())
                codes = set()
                for _ in range(n):
                    codes.add(client.get(path, params=params).status_code)
                self.assertIn(200, codes, f"{path} must still serve anonymous reads")
                self.assertIn(429, codes, f"{path} must rate-limit a hammering client")

    def test_anonymous_reads_are_preserved_on_new_routes(self):
        client = TestClient(_mini_app())
        self.assertEqual(client.get("/candle/latest", params={"ticker": "ZZANON"}).status_code, 200)
        self.assertEqual(client.get("/range/paginated", params={"ticker": "ZZANON"}).status_code, 200)
        self.assertEqual(
            client.get("/intraday/since", params={"ticker": "ZZANON", "since": 1}).status_code, 200)

    def test_oversized_ticker_is_rejected(self):
        client = TestClient(_mini_app())
        self.assertEqual(client.get("/candle/latest", params={"ticker": "A" * 41}).status_code, 422)
        self.assertEqual(client.get("/range/paginated", params={"ticker": "A" * 41}).status_code, 422)
        self.assertEqual(
            client.get("/intraday/since", params={"ticker": "A" * 41, "since": 1}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
