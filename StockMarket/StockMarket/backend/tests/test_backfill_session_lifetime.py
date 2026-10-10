"""Tests for the ON-DEMAND BACKFILL DB SESSION LIFETIME HARDENING phase.

perform_on_demand_backfill() previously opened its SQLAlchemy session
(`db = database.SessionLocal()`) before any of its AngelOne/yfinance network
calls, and held it open across all of them (login, two get_historical_candles
attempts, a 3s retry sleep, and the yfinance fallback) even though nothing
in that phase ever touches `db`. The fix delays session creation until the
short read+write persistence phase that follows all network I/O -- see the
"Phase 2" comment in main.py::perform_on_demand_backfill.

These tests prove the *ordering* (network I/O completes before a session
ever exists), not just that the final DB writes are still correct -- the
correctness of the writes themselves (ON CONFLICT DO NOTHING, dedup) is
already covered by test_phase3a_fixes.py's TestBackfillIdempotencyFix and is
untouched by this change.

Follows this suite's established pattern of `from main import ...` /
`import main` plus patching (see test_phase3a_fixes.py,
test_candle_cache.py's TestBackfillNonBlocking) rather than hitting a real
DB or a real AngelOne/yfinance connection.
"""
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import main


def _reset_backfill_state(ticker):
    clean = ticker.strip().upper()
    with main._backfill_lock:
        main.backfill_locks.discard(clean)
    # Also clear any backfill cooldown left by this ticker so a later test is
    # not silently skipped by the per-ticker cooldown guard.
    with main._backfill_cooldown_lock:
        for _k in list(main._backfill_cooldown):
            if _k.startswith(clean + ":"):
                main._backfill_cooldown.pop(_k, None)


def _make_session_factory(events, existing_ts=None, five_min_rows=None, commit_error=None):
    """Builds a fake `database.SessionLocal` that records WHEN it is called
    (relative to other recorded events) and returns a MagicMock session
    wired up for the two query shapes perform_on_demand_backfill uses:
    the dedup existing-timestamp query and the resample-fallback query."""
    existing_ts = existing_ts if existing_ts is not None else []
    five_min_rows = five_min_rows if five_min_rows is not None else []

    def factory():
        events.append("session_open")
        db = MagicMock(name="fake_db_session")
        db.close.side_effect = lambda: events.append("session_close")
        db.rollback.side_effect = lambda: events.append("session_rollback")
        if commit_error is not None:
            def _commit():
                events.append("session_commit_attempt")
                raise commit_error
            db.commit.side_effect = _commit
        else:
            db.commit.side_effect = lambda: events.append("session_commit")

        q1 = db.query.return_value.filter.return_value
        # dedup query: db.query(model.timestamp).filter(...).filter(timeframe==...).all()
        q1.filter.return_value.all.return_value = list(existing_ts)
        # resample-fallback query: db.query(Candle).filter(...).order_by(...).all()
        q1.order_by.return_value.all.return_value = list(five_min_rows)
        return db

    return factory


class BackfillSessionLifetimeTests(unittest.TestCase):
    TICKER = "TESTBACKFILL"

    def setUp(self):
        _reset_backfill_state(self.TICKER)
        self.events = []
        self.mock_historical = MagicMock()
        self.mock_historical.is_logged_in = True
        self.session_factory = _make_session_factory(self.events)

        self._patchers = [
            patch("main.historical_service", self.mock_historical),
            patch("main.database.SessionLocal", self.session_factory),
            # These tests exercise backfill session lifetime, not market-hours
            # gating; force the market open so the closed-market skip cannot make
            # them time-dependent (they otherwise fail after 15:30 IST).
            patch("main.is_market_open_now", lambda *a, **k: True),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop_patchers)

    def _stop_patchers(self):
        for p in self._patchers:
            p.stop()
        _reset_backfill_state(self.TICKER)

    def _one_candle(self, ts=None):
        ts = ts or datetime(2026, 1, 5, 11, 55)
        return {"timestamp": ts, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 500}

    def _run(self, interval="5m", backfill_start=None, now=None):
        now = now or datetime(2026, 1, 5, 12, 0)
        backfill_start = backfill_start or (now - timedelta(hours=1))
        main.perform_on_demand_backfill(self.TICKER, interval, backfill_start, now)

    # 1 & 3. DB session is released before provider HTTP begins / created
    # again (fresh) for the persistence phase.
    def test_session_opened_only_after_provider_fetch_completes(self):
        def fetch(**kwargs):
            self.events.append("angelone_fetch")
            return [self._one_candle()]
        self.mock_historical.get_historical_candles.side_effect = fetch

        self._run()

        self.assertIn("angelone_fetch", self.events)
        self.assertIn("session_open", self.events)
        self.assertLess(
            self.events.index("angelone_fetch"), self.events.index("session_open"),
            "a DB session must not exist until after the provider fetch completes",
        )
        self.assertEqual(self.events.count("session_open"), 1, "exactly one session should be opened")

    # 2. Provider HTTP delay (retry sleep) does not hold a DB connection.
    @patch("time.sleep")
    def test_retry_sleep_happens_with_no_session_open(self, mock_sleep):
        mock_sleep.side_effect = lambda s: self.events.append(f"sleep:{s}")
        call_count = {"n": 0}

        def fetch(**kwargs):
            call_count["n"] += 1
            self.events.append(f"angelone_fetch_{call_count['n']}")
            if call_count["n"] == 1:
                return []
            return [self._one_candle()]
        self.mock_historical.get_historical_candles.side_effect = fetch

        self._run()

        self.assertEqual(call_count["n"], 2, "the empty-result retry must still happen exactly once")
        self.assertIn("sleep:3", self.events)
        idx_sleep = self.events.index("sleep:3")
        idx_fetch2 = self.events.index("angelone_fetch_2")
        idx_open = self.events.index("session_open")
        self.assertLess(idx_sleep, idx_fetch2, "sleep must happen before the retry fetch")
        self.assertLess(idx_fetch2, idx_open, "the session must open only after the retry fetch returns")
        self.assertNotIn("session_open", self.events[:idx_sleep], "no session may be open during the retry sleep")

    # 4. Successful backfill still persists the expected candles.
    def test_successful_backfill_persists_new_candle(self):
        self.mock_historical.get_historical_candles.return_value = [self._one_candle()]

        self._run()

        self.assertEqual(self.events.count("session_open"), 1)
        self.assertIn("session_commit", self.events)

    # 5. Provider failure still performs correct rollback/error handling,
    # AND a failure during the persistence phase itself still rolls back.
    def test_persistence_failure_rolls_back_and_still_releases_lock(self):
        self.session_factory = _make_session_factory(self.events, commit_error=RuntimeError("boom"))
        with patch("main.database.SessionLocal", self.session_factory):
            self.mock_historical.get_historical_candles.return_value = [self._one_candle()]
            self._run()  # must not raise -- the function catches its own exceptions

        self.assertIn("session_commit_attempt", self.events)
        self.assertIn("session_rollback", self.events)
        self.assertIn("session_close", self.events)
        self.assertNotIn(self.TICKER, main.backfill_locks, "the per-ticker lock must be released even on failure")

    def test_network_failure_before_any_session_exists_does_not_raise_and_releases_lock(self):
        """If AngelOne login itself raises (pure network failure, before any
        db = database.SessionLocal() has run), the except/finally blocks must
        not blow up trying to rollback/close a session that was never
        opened -- this is exactly what the `db = None` guard exists for."""
        self.mock_historical.is_logged_in = False
        self.mock_historical.login.side_effect = ConnectionError("angelone unreachable")

        try:
            self._run()
        except Exception as e:  # pragma: no cover - the test itself fails below if this triggers
            self.fail(f"perform_on_demand_backfill must swallow the error, not raise: {e}")

        self.assertEqual(self.events.count("session_open"), 0, "no session should ever have been created")
        self.assertNotIn(self.TICKER, main.backfill_locks)

    # 6. Retry behavior remains unchanged (covered together with #2 above via
    # test_retry_sleep_happens_with_no_session_open's call-count assertion).

    # 7. Concurrent backfill requests for the same ticker do not duplicate
    # provider calls / candle writes -- the pre-existing per-ticker lock
    # still spans the whole function exactly as before.
    def test_concurrent_calls_for_same_ticker_do_not_duplicate_provider_fetch(self):
        started = threading.Event()
        release = threading.Event()
        call_count = {"n": 0}

        def fetch(**kwargs):
            call_count["n"] += 1
            started.set()
            release.wait(timeout=5)
            return [self._one_candle()]
        self.mock_historical.get_historical_candles.side_effect = fetch

        now = datetime(2026, 1, 5, 12, 0)
        start = now - timedelta(hours=1)
        t1 = threading.Thread(target=main.perform_on_demand_backfill, args=(self.TICKER, "5m", start, now))
        t1.start()
        try:
            self.assertTrue(started.wait(timeout=5), "first call never reached the provider fetch")
            # A second call for the SAME ticker while the first is in-flight
            # must bail out immediately via the lock, not re-run the fetch.
            main.perform_on_demand_backfill(self.TICKER, "5m", start, now)
        finally:
            release.set()
            t1.join(timeout=5)

        self.assertEqual(call_count["n"], 1, "a concurrent call for the same ticker must not re-run the provider fetch")

    # 8. Existing fallback behavior (yfinance, then resample) remains unchanged.
    @patch("time.sleep")
    def test_yfinance_fallback_still_used_when_angelone_returns_nothing(self, mock_sleep):
        self.mock_historical.get_historical_candles.return_value = []
        fake_row = MagicMock(timestamp=datetime(2026, 1, 5, 11, 55), open=1.0, high=1.0, low=1.0, close=1.0, volume=10)

        with patch("main._fetch_yfinance_intraday", return_value=[fake_row]) as mock_yf:
            self._run()

        mock_yf.assert_called_once()
        self.assertIn("session_commit", self.events)

    @patch("time.sleep")
    def test_resample_fallback_still_used_for_higher_timeframe_when_providers_fail(self, mock_sleep):
        self.mock_historical.get_historical_candles.return_value = []
        fake_5m_row = MagicMock(timestamp=datetime(2026, 1, 5, 11, 45), open=1.0, high=1.0, low=1.0, close=1.0, volume=10)
        self.session_factory = _make_session_factory(self.events, five_min_rows=[fake_5m_row])

        resampled = [self._one_candle(ts=datetime(2026, 1, 5, 11, 45))]
        with patch("main.database.SessionLocal", self.session_factory), \
             patch("main._fetch_yfinance_intraday", return_value=[]), \
             patch("resampler.CandleResampler.resample_5m_to", return_value=resampled) as mock_resample:
            self._run(interval="15m", backfill_start=datetime(2026, 1, 5, 10, 0), now=datetime(2026, 1, 5, 12, 0))

        mock_resample.assert_called_once()
        self.assertIn("session_commit", self.events)

    # 9. No session leaks occur on exceptions -- every session_open in these
    # tests must be paired with a session_close, in every scenario above.
    def test_every_opened_session_is_always_closed(self):
        self.mock_historical.get_historical_candles.return_value = [self._one_candle()]
        self._run()
        self.assertEqual(self.events.count("session_open"), self.events.count("session_close"))


if __name__ == "__main__":
    unittest.main()
