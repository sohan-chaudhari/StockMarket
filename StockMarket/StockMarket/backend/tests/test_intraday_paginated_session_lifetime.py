"""Tests for the ON-DEMAND BACKFILL REQUEST-HANDLER SESSION SCOPING phase.

get_intraday_paginated()'s own `db: Session = Depends(get_db)` runs a real
query (`records = q...all()`, main.py:4675) BEFORE it calls
perform_on_demand_backfill(). Proven with real engine.pool.checkedout()
instrumentation: that query checks out a genuine pooled connection which,
without this fix, stayed checked out -- idle, uncommitted -- for the entire
duration of perform_on_demand_backfill's AngelOne/yfinance network I/O
(unlike the *previous* phase's inner-function session, which was proven to
never hold a connection during network I/O at all). The fix is a single
`db.close()` immediately before that call, on the gap-fill path only: a
SQLAlchemy Session stays fully usable after close() -- its next query
(built from the SAME pre-close `q_base` Query object) transparently opens a
fresh connection. See main.py's HARDEN-XX comment right above that call.

These tests use the real configured database.engine/SessionLocal -- not
SQLite or a mocked session -- specifically because the point is to prove
real connection-pool behavior (engine.pool.checkedout()), not merely that a
Session object exists or gets a close() call recorded on a mock. All writes
use one unique, obviously-fake ticker and are deleted in setUp/tearDown, so
this never touches real market data.

perform_on_demand_backfill's OWN internal session lifetime was already
proven separately in test_backfill_session_lifetime.py and is untouched
here; most tests below patch it directly (no real AngelOne/yfinance calls),
except the concurrency tests, which patch only historical_service /
_fetch_yfinance_intraday so the function's real per-ticker lock actually
runs end-to-end through the real endpoint call.
"""
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import database
import models
import main

TEST_TICKER = "ZZTESTSESSLIFECYCLE"
TEST_TICKER_2 = "ZZTESTSESSLIFECYCLE2"


def _postgres_reachable():
    try:
        conn = database.engine.connect()
        conn.close()
        return True
    except Exception:
        return False


POSTGRES_AVAILABLE = _postgres_reachable()


def _delete_test_candles(ticker):
    db = database.SessionLocal()
    try:
        db.query(models.Candle).filter(models.Candle.ticker == ticker).delete()
        db.commit()
    finally:
        db.close()


def _insert_candle(ticker, timeframe, ts, price=100.0):
    db = database.SessionLocal()
    try:
        db.add(models.Candle(
            ticker=ticker, timeframe=timeframe, timestamp=ts,
            open=price, high=price, low=price, close=price, volume=100,
            is_completed=True,
        ))
        db.commit()
    finally:
        db.close()


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class IntradayPaginatedSessionLifetimeTests(unittest.TestCase):

    def setUp(self):
        for t in (TEST_TICKER, TEST_TICKER_2):
            _delete_test_candles(t)
            with main._backfill_lock:
                main.backfill_locks.discard(t)
            # Clear the per-ticker backfill cooldown so it cannot leak between
            # tests (the cooldown guard now also gates dispatch).
            with main._backfill_cooldown_lock:
                for _k in list(main._backfill_cooldown):
                    if _k.startswith(t + ":"):
                        main._backfill_cooldown.pop(_k, None)

    def tearDown(self):
        for t in (TEST_TICKER, TEST_TICKER_2):
            _delete_test_candles(t)
            with main._backfill_lock:
                main.backfill_locks.discard(t)
            # Clear the per-ticker backfill cooldown so it cannot leak between
            # tests (the cooldown guard now also gates dispatch).
            with main._backfill_cooldown_lock:
                for _k in list(main._backfill_cooldown):
                    if _k.startswith(t + ":"):
                        main._backfill_cooldown.pop(_k, None)

    def _call(self, db, ticker=TEST_TICKER, **overrides):
        kwargs = dict(ticker=ticker, interval="5m", before=None, after=None,
                       limit=100, background_tasks=None, db=db)
        kwargs.update(overrides)
        return main.get_intraday_paginated(**kwargs)

    # 1 & 2. DB session released before provider I/O; real connection is not
    # checked out during the provider fetch.
    def test_connection_released_before_backfill_network_io(self):
        pool = database.engine.pool
        observed = {}

        def fake_backfill(ticker, interval, backfill_start, now):
            observed["checkedout_during_backfill"] = pool.checkedout()
            time.sleep(0.2)  # stand-in for AngelOne/yfinance I/O

        db = database.SessionLocal()
        try:
            with patch("main.perform_on_demand_backfill", side_effect=fake_backfill) as mock_bf:
                result = self._call(db)
        finally:
            db.close()

        mock_bf.assert_called_once()
        self.assertEqual(
            observed["checkedout_during_backfill"], 0,
            "the request session's connection must be released before backfill's network I/O runs",
        )
        self.assertEqual(result, {"data": [], "has_more": False})

    # 3. DB session is reopened (transparently, via the same pre-close Query
    # object) when persistence/read-after-backfill is required, and returns
    # correct, freshly-written data -- not stale/cached data.
    def test_post_backfill_requery_returns_freshly_written_candle(self):
        now = datetime(2026, 1, 5, 12, 0)
        candle_ts = now - timedelta(minutes=5)

        def fake_backfill(ticker, interval, backfill_start, now_arg):
            _insert_candle(TEST_TICKER, "5m", candle_ts, price=123.45)

        db = database.SessionLocal()
        try:
            with patch("main.database.get_ist_now", return_value=now), \
                 patch("main.perform_on_demand_backfill", side_effect=fake_backfill):
                result = self._call(db)
        finally:
            db.close()

        self.assertEqual(len(result["data"]), 1)
        self.assertAlmostEqual(result["data"][0]["close"], 123.45, places=2)

    # 5. Backfill failure preserves existing endpoint behavior: swallowed,
    # response still returns normally, no session leak.
    def test_backfill_exception_is_swallowed_and_response_still_returns(self):
        pool = database.engine.pool

        def fake_backfill(*a, **kw):
            raise ConnectionError("angelone unreachable")

        db = database.SessionLocal()
        try:
            with patch("main.perform_on_demand_backfill", side_effect=fake_backfill):
                result = self._call(db)  # must not raise
        finally:
            db.close()  # must not raise either -- proves no broken/leaked session state

        self.assertEqual(result, {"data": [], "has_more": False})
        self.assertEqual(pool.checkedout(), 0)

    # 4. Successful (no-gap) request behavior is completely unchanged: the
    # fast path never calls backfill and its connection is held continuously,
    # exactly as before this phase.
    def test_no_gap_path_never_calls_backfill_and_keeps_its_connection(self):
        now = datetime(2026, 1, 5, 12, 0)
        _insert_candle(TEST_TICKER, "5m", now - timedelta(minutes=1), price=50.0)
        pool = database.engine.pool

        db = database.SessionLocal()
        try:
            with patch("main.database.get_ist_now", return_value=now), \
                 patch("main.perform_on_demand_backfill") as mock_bf:
                result = self._call(db)
                checked_out_immediately_after_call = pool.checkedout()
        finally:
            db.close()

        mock_bf.assert_not_called()
        self.assertEqual(
            checked_out_immediately_after_call, 1,
            "the connection opened by the initial query must still be held on the unchanged fast path",
        )
        self.assertEqual(len(result["data"]), 1)

    # 11 & 12. Historical pagination (before= set, not the latest window)
    # never enters the gap-fill block at all -- pagination ordering and
    # response shape for that path are untouched by this phase.
    def test_historical_pagination_never_triggers_backfill(self):
        now = datetime(2026, 1, 5, 12, 0)
        old_ts = now - timedelta(days=5)
        _insert_candle(TEST_TICKER, "5m", old_ts, price=77.0)
        before_epoch = main._ts_to_epoch(now - timedelta(hours=1))

        db = database.SessionLocal()
        try:
            with patch("main.database.get_ist_now", return_value=now), \
                 patch("main.perform_on_demand_backfill") as mock_bf:
                result = self._call(db, before=before_epoch)
        finally:
            db.close()

        mock_bf.assert_not_called()
        self.assertEqual(len(result["data"]), 1)
        self.assertAlmostEqual(result["data"][0]["close"], 77.0, places=2)

    # 8. Same-ticker concurrent requests remain safe: the real per-ticker
    # lock (untouched by this phase) still lets only one request actually
    # run the provider fetch, proven end-to-end through the real endpoint
    # call (not a reimplementation of the lock).
    def test_concurrent_requests_for_same_ticker_do_not_duplicate_provider_fetch(self):
        started = threading.Event()
        release = threading.Event()
        call_count = {"n": 0}
        mock_historical = MagicMock()
        mock_historical.is_logged_in = True

        def fetch(**kwargs):
            call_count["n"] += 1
            started.set()
            release.wait(timeout=5)
            return []
        mock_historical.get_historical_candles.side_effect = fetch

        now = datetime(2026, 1, 5, 12, 0)
        results = {}

        def worker(name):
            db = database.SessionLocal()
            try:
                results[name] = self._call(db)
            finally:
                db.close()

        with patch("main.historical_service", mock_historical), \
             patch("main.database.get_ist_now", return_value=now), \
             patch("main._fetch_yfinance_intraday", return_value=[]), \
             patch("time.sleep"):
            t1 = threading.Thread(target=worker, args=("t1",))
            t1.start()
            self.assertTrue(started.wait(timeout=5), "first request never reached the provider fetch")
            worker("t2")  # second "concurrent request" for the SAME ticker, run inline while t1 is blocked
            release.set()
            t1.join(timeout=5)

        # t1 alone makes 2 calls (initial attempt + the empty-result retry);
        # if the lock had failed, t2 would have added 2 more (4 total).
        self.assertEqual(
            call_count["n"], 2,
            "a concurrent request for the same ticker must not re-run the provider fetch",
        )
        self.assertIn("t1", results)
        self.assertIn("t2", results)

    # 9. Different tickers remain independently concurrent where previously
    # allowed -- the per-ticker lock must not serialize unrelated tickers.
    def test_concurrent_requests_for_different_tickers_both_run_provider_fetch(self):
        call_tickers = []
        lock = threading.Lock()
        mock_historical = MagicMock()
        mock_historical.is_logged_in = True

        def fetch(**kwargs):
            with lock:
                call_tickers.append(kwargs.get("ticker"))
            time.sleep(0.1)
            return []
        mock_historical.get_historical_candles.side_effect = fetch

        now = datetime(2026, 1, 5, 12, 0)
        results = {}

        def worker(name, ticker):
            db = database.SessionLocal()
            try:
                results[name] = self._call(db, ticker=ticker)
            finally:
                db.close()

        with patch("main.historical_service", mock_historical), \
             patch("main.database.get_ist_now", return_value=now), \
             patch("main._fetch_yfinance_intraday", return_value=[]), \
             patch("time.sleep"):
            t1 = threading.Thread(target=worker, args=("t1", TEST_TICKER))
            t2 = threading.Thread(target=worker, args=("t2", TEST_TICKER_2))
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        self.assertIn(TEST_TICKER, call_tickers)
        self.assertIn(TEST_TICKER_2, call_tickers)
        self.assertIn("t1", results)
        self.assertIn("t2", results)

    # 10. No DB session/connection leaks across a full, real call.
    def test_pool_returns_to_baseline_after_a_full_gap_fill_call(self):
        pool = database.engine.pool
        baseline = pool.checkedout()

        db = database.SessionLocal()
        try:
            with patch("main.perform_on_demand_backfill"):
                self._call(db)
        finally:
            db.close()

        self.assertEqual(pool.checkedout(), baseline)


if __name__ == "__main__":
    unittest.main()
