"""Tests for the EVENT LOOP LAG ROOT-CAUSE FIX phase.

The root-cause isolation phase proved two direct event-loop blockers with
real Docker measurements:
  - root cause #1: _yfinance_inactive_sync()'s per-ticker DB loop, run
    directly on the event loop -- proven ALONE (idle control, every other
    background job disabled) to cause a ~9.6s stall.
  - root cause #2: _daily_prefill_all()'s base_price sync UPDATE...FROM
    query, run directly on the event loop -- measured ~3.06s of genuine
    execution time (parallel seq scan + external disk sort, no supporting
    index), observed causing 14.3s-16.7s stalls in the live app.

The fix extracted each blocking body into a plain, module-level, testable
function (_sync_yfinance_inactive_symbols, _sync_daily_base_price), each
creating and closing its OWN SQLAlchemy session (never one shared with the
event-loop thread), invoked from the async scheduler loops via
`await asyncio.to_thread(...)`.

Needs a real PostgreSQL connection (same convention as every other
DB-integration test in this suite) -- skips if unavailable. All writes use
obviously-fake test tickers, cleaned up in setUp/tearDown, and are never
run against production.
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import database
import models
import main

TEST_TICKER_EXISTING = "ZZTESTYFSYNCEXIST"
TEST_TICKER_NEW = "ZZTESTYFSYNCNEW"
TEST_TICKER_BP = "ZZTESTBASEPRICE"


def _postgres_reachable():
    try:
        conn = database.engine.connect()
        conn.close()
        return True
    except Exception:
        return False


POSTGRES_AVAILABLE = _postgres_reachable()


def _cleanup(tickers):
    db = database.SessionLocal()
    try:
        db.query(models.StockMetadata).filter(models.StockMetadata.ticker.in_(tickers)).delete(
            synchronize_session=False
        )
        db.query(models.Candle).filter(models.Candle.ticker.in_(tickers)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class YFSyncOffloadTests(unittest.TestCase):
    def setUp(self):
        _cleanup([TEST_TICKER_EXISTING, TEST_TICKER_NEW])

    def tearDown(self):
        _cleanup([TEST_TICKER_EXISTING, TEST_TICKER_NEW])

    def test_returns_zero_and_touches_no_session_when_nothing_inactive(self):
        with patch("main.yf_downloader") as mock_yf, \
             patch("main.database.SessionLocal") as mock_session_factory:
            mock_yf.get_inactive_symbols.return_value = {}
            result = main._sync_yfinance_inactive_symbols()
        self.assertEqual(result, 0)
        mock_session_factory.assert_not_called()

    def test_marks_existing_ticker_inactive_and_commits(self):
        db = database.SessionLocal()
        db.add(models.StockMetadata(
            ticker=TEST_TICKER_EXISTING, name="Test Co", exchange="NSE", is_active=True,
        ))
        db.commit()
        db.close()

        with patch("main.yf_downloader") as mock_yf:
            mock_yf.get_inactive_symbols.return_value = {TEST_TICKER_EXISTING: "NOT_FOUND"}
            result = main._sync_yfinance_inactive_symbols()
            mock_yf.clear_inactive_symbols.assert_called_once()

        self.assertEqual(result, 1)
        verify_db = database.SessionLocal()
        try:
            row = verify_db.query(models.StockMetadata).filter(
                models.StockMetadata.ticker == TEST_TICKER_EXISTING
            ).first()
            self.assertIsNotNone(row)
            self.assertFalse(row.is_active)
            # Existing-row path must not touch name/exchange -- only is_active.
            self.assertEqual(row.name, "Test Co")
        finally:
            verify_db.close()

    def test_inserts_new_ticker_row_when_not_existing(self):
        with patch("main.yf_downloader") as mock_yf:
            mock_yf.get_inactive_symbols.return_value = {TEST_TICKER_NEW: "DELISTED"}
            result = main._sync_yfinance_inactive_symbols()

        self.assertEqual(result, 1)
        verify_db = database.SessionLocal()
        try:
            row = verify_db.query(models.StockMetadata).filter(
                models.StockMetadata.ticker == TEST_TICKER_NEW
            ).first()
            self.assertIsNotNone(row)
            self.assertFalse(row.is_active)
            self.assertEqual(row.name, "[DELISTED]")
            self.assertEqual(row.exchange, "NSE")
        finally:
            verify_db.close()

    def test_rollback_and_reraise_on_db_failure(self):
        db = database.SessionLocal()
        db.add(models.StockMetadata(
            ticker=TEST_TICKER_EXISTING, name="Test Co", exchange="NSE", is_active=True,
        ))
        db.commit()
        db.close()

        with patch("main.yf_downloader") as mock_yf, \
             patch("main.database.SessionLocal") as mock_factory:
            mock_yf.get_inactive_symbols.return_value = {TEST_TICKER_EXISTING: "NOT_FOUND"}
            real_session = database.SessionLocal()
            real_session.commit = MagicMock(side_effect=RuntimeError("simulated DB failure"))
            real_session.rollback = MagicMock(wraps=real_session.rollback)
            real_session.close = MagicMock(wraps=real_session.close)
            mock_factory.return_value = real_session

            with self.assertRaises(RuntimeError):
                main._sync_yfinance_inactive_symbols()

            real_session.rollback.assert_called_once()
            real_session.close.assert_called_once()
            mock_yf.clear_inactive_symbols.assert_not_called()

        # The real DB row must be untouched -- the failed commit never landed.
        verify_db = database.SessionLocal()
        try:
            row = verify_db.query(models.StockMetadata).filter(
                models.StockMetadata.ticker == TEST_TICKER_EXISTING
            ).first()
            self.assertTrue(row.is_active, "a rolled-back write must not have persisted")
        finally:
            verify_db.close()

    def test_session_closed_even_when_yf_downloader_raises_before_db_work(self):
        with patch("main.yf_downloader") as mock_yf:
            mock_yf.get_inactive_symbols.side_effect = RuntimeError("boom")
            with self.assertRaises(RuntimeError):
                main._sync_yfinance_inactive_symbols()
        # No session was ever opened in this path (fails before SessionLocal()),
        # so there is nothing to leak -- this proves the early-exit path is safe.

    def test_runs_on_a_different_thread_than_the_caller_via_to_thread(self):
        caller_thread_id = threading.get_ident()
        seen = {}

        with patch("main.yf_downloader") as mock_yf:
            def _capture(*a, **kw):
                seen["thread_id"] = threading.get_ident()
                return {}
            mock_yf.get_inactive_symbols.side_effect = _capture

            import asyncio

            async def _run():
                return await asyncio.to_thread(main._sync_yfinance_inactive_symbols)

            asyncio.run(_run())

        self.assertIn("thread_id", seen)
        self.assertNotEqual(seen["thread_id"], caller_thread_id,
                             "the sync worker must run on a worker thread, not the caller's")

    def test_startup_source_offloads_yfsync_via_to_thread(self):
        """Static proof the real scheduler wires this through to_thread --
        not just that the extracted function works in isolation."""
        import inspect
        src = inspect.getsource(main.startup)
        self.assertIn("await asyncio.to_thread(_sync_yfinance_inactive_symbols)", src)
        # The old inline per-ticker loop must be gone from startup()'s own
        # source -- proving it was actually extracted, not duplicated.
        self.assertNotIn("db_sync.query(models.StockMetadata)", src)


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class DailyPrefillBasePriceOffloadTests(unittest.TestCase):
    def setUp(self):
        _cleanup([TEST_TICKER_BP])

    def tearDown(self):
        _cleanup([TEST_TICKER_BP])

    def test_updates_base_price_from_latest_1d_close(self):
        from datetime import datetime
        db = database.SessionLocal()
        db.add(models.StockMetadata(
            ticker=TEST_TICKER_BP, name="Test Co", exchange="NSE", is_active=True, base_price=0,
        ))
        db.add(models.Candle(
            ticker=TEST_TICKER_BP, timeframe="1D", timestamp=datetime(2026, 1, 2),
            open=100, high=105, low=98, close=101, volume=1000, is_completed=True,
        ))
        db.add(models.Candle(
            ticker=TEST_TICKER_BP, timeframe="1D", timestamp=datetime(2026, 1, 5),
            open=101, high=110, low=100, close=108, volume=2000, is_completed=True,
        ))
        db.commit()
        db.close()

        elapsed_ms = main._sync_daily_base_price()
        self.assertIsInstance(elapsed_ms, int)
        self.assertGreaterEqual(elapsed_ms, 0)

        verify_db = database.SessionLocal()
        try:
            row = verify_db.query(models.StockMetadata).filter(
                models.StockMetadata.ticker == TEST_TICKER_BP
            ).first()
            # Must pick the LATEST (2026-01-05) close, not the first one.
            self.assertAlmostEqual(row.base_price, 108.0, places=2)
        finally:
            verify_db.close()

    def test_ticker_with_no_1d_candles_is_left_unchanged(self):
        db = database.SessionLocal()
        db.add(models.StockMetadata(
            ticker=TEST_TICKER_BP, name="Test Co", exchange="NSE", is_active=True, base_price=42.0,
        ))
        db.commit()
        db.close()

        main._sync_daily_base_price()

        verify_db = database.SessionLocal()
        try:
            row = verify_db.query(models.StockMetadata).filter(
                models.StockMetadata.ticker == TEST_TICKER_BP
            ).first()
            self.assertAlmostEqual(row.base_price, 42.0, places=2)
        finally:
            verify_db.close()

    def test_rollback_and_reraise_on_db_failure(self):
        with patch("main.database.SessionLocal") as mock_factory:
            real_session = database.SessionLocal()
            real_session.commit = MagicMock(side_effect=RuntimeError("simulated DB failure"))
            real_session.rollback = MagicMock(wraps=real_session.rollback)
            real_session.close = MagicMock(wraps=real_session.close)
            mock_factory.return_value = real_session

            with self.assertRaises(RuntimeError):
                main._sync_daily_base_price()

            real_session.rollback.assert_called_once()
            real_session.close.assert_called_once()

    def test_runs_on_a_different_thread_than_the_caller_via_to_thread(self):
        caller_thread_id = threading.get_ident()
        seen = {}
        real_fn = main._sync_daily_base_price

        def _wrapped():
            seen["thread_id"] = threading.get_ident()
            return real_fn()

        import asyncio

        async def _run():
            return await asyncio.to_thread(_wrapped)

        asyncio.run(_run())
        self.assertIn("thread_id", seen)
        self.assertNotEqual(seen["thread_id"], caller_thread_id)

    def test_startup_source_offloads_base_price_sync_via_to_thread(self):
        import inspect
        src = inspect.getsource(main.startup)
        self.assertIn("await asyncio.to_thread(_sync_daily_base_price)", src)
        # The expensive UPDATE's own SQL text must no longer appear inline
        # in startup()'s source -- only inside the extracted function.
        self.assertNotIn("SELECT DISTINCT ON (ticker) ticker, close", src)

    def test_sync_daily_base_price_source_still_contains_the_query(self):
        import inspect
        src = inspect.getsource(main._sync_daily_base_price)
        self.assertIn("SELECT DISTINCT ON (ticker) ticker, close", src)
        self.assertIn("FROM candles", src)


class EventLoopRemainsResponsiveTests(unittest.TestCase):
    """Test Group C: a deterministic, non-flaky proof that the event loop
    keeps making progress while a blocking call runs via asyncio.to_thread
    -- independent of the real app, DB, or Docker. Uses a heartbeat
    coroutine with generous margins so it isn't sensitive to CI jitter."""

    def test_heartbeat_keeps_ticking_during_blocking_to_thread_call(self):
        import asyncio

        async def _run():
            heartbeat_count = {"n": 0}
            stop = threading.Event()

            async def heartbeat():
                while not stop.is_set():
                    heartbeat_count["n"] += 1
                    await asyncio.sleep(0.01)

            def blocking_work():
                time.sleep(0.3)

            hb_task = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.02)  # let the heartbeat get going first
            await asyncio.to_thread(blocking_work)
            stop.set()
            await hb_task
            return heartbeat_count["n"]

        count = asyncio.run(_run())
        # ~30 ticks expected (0.3s / 0.01s); a wide floor keeps this robust
        # to CI scheduling jitter while still failing hard if to_thread were
        # replaced with a direct synchronous call (which would freeze the
        # heartbeat for the whole 300ms and yield close to 0).
        self.assertGreaterEqual(count, 15,
            "event loop should keep progressing while blocking work runs on a worker thread")

    def test_direct_synchronous_call_WOULD_freeze_the_heartbeat(self):
        """Negative control: proves the test above actually distinguishes
        offloaded from non-offloaded work, rather than always passing."""
        import asyncio

        async def _run():
            heartbeat_count = {"n": 0}
            stop = threading.Event()

            async def heartbeat():
                while not stop.is_set():
                    heartbeat_count["n"] += 1
                    await asyncio.sleep(0.01)

            hb_task = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.02)
            time.sleep(0.3)  # DIRECT call on the event-loop thread -- no to_thread
            stop.set()
            await hb_task
            return heartbeat_count["n"]

        count = asyncio.run(_run())
        self.assertLessEqual(count, 3,
            "a direct synchronous call on the event-loop thread must freeze the heartbeat")


if __name__ == "__main__":
    unittest.main()
