"""
Tests for intraday_prefill.py

Coverage:
  _fetch_candles  — retry logic, interval/exchange/raise_on_error params
  _insert_candles — DO NOTHING constraint, rowcount accounting, SQL content,
                    timezone normalisation (IST-aware → IST-naive)
  prefill_due_today — trading-day gate, DB status gate
  run_prefill_session — skip/no-data/partial/error orchestration, idempotency

Patching convention:
  Local-import symbols (SessionLocal, nse_calendar, HistoricalDataService, …)
  are patched on their *source* module (database.SessionLocal, etc.) because
  `from X import Y` inside a function re-reads X.Y at call time.
  Module-level functions defined in intraday_prefill (_get_universe, etc.)
  are patched on intraday_prefill directly.
"""

import threading
import unittest
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from unittest.mock import MagicMock, call, patch

import intraday_prefill as _mod
from intraday_prefill import (
    COMPLETE_CANDLES,
    RETRY_DELAYS,
    _fetch_candles,
    _insert_candles,
    prefill_due_today,
    run_prefill_session,
)

_IST = timezone(timedelta(hours=5, minutes=30))
_D   = date(2026, 8, 18)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candles(n: int, d: date = _D) -> list:
    """Build n synthetic 5m candle dicts starting at 09:15 IST."""
    base = datetime(d.year, d.month, d.day, 9, 15, tzinfo=_IST)
    return [
        {
            "timestamp": base + timedelta(minutes=5 * i),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 1000,
        }
        for i in range(n)
    ]


def _mock_session_ctx(rowcount: int = 1):
    """Return (ctx, db) where ctx is a context-manager that yields db."""
    db  = MagicMock()
    db.execute.return_value.rowcount = rowcount
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__  = MagicMock(return_value=False)
    return ctx, db


# ── _fetch_candles ────────────────────────────────────────────────────────────

class TestFetchCandles(unittest.TestCase):
    def setUp(self):
        self.svc = MagicMock()
        # Bypass real rate-gate sleeps so tests run instantly.
        self._sleep_p = patch.object(_mod.time, "sleep")
        self._mono_p  = patch.object(_mod.time, "monotonic", return_value=999.0)
        self._sleep_p.start()
        self._mono_p.start()
        _mod._last_api_time = 0.0   # reset module-level rate-gate state

    def tearDown(self):
        self._sleep_p.stop()
        self._mono_p.stop()

    def test_success_returns_full_list(self):
        self.svc.get_historical_candles.return_value = _candles(75)
        result = _fetch_candles(self.svc, "TCS", _D)
        self.assertEqual(len(result), 75)
        self.svc.get_historical_candles.assert_called_once()

    def test_empty_response_is_not_an_error(self):
        """Angel One returning [] means no data — not a fetch failure."""
        self.svc.get_historical_candles.return_value = []
        result = _fetch_candles(self.svc, "NEWCO", _D)
        self.assertEqual(result, [])
        self.svc.get_historical_candles.assert_called_once()

    def test_permanent_error_raises_immediately(self):
        from historical_service import HistoricalFetchError
        self.svc.get_historical_candles.side_effect = HistoricalFetchError(
            "not found", retryable=False
        )
        with self.assertRaises(HistoricalFetchError) as ctx:
            _fetch_candles(self.svc, "INVALID", _D)
        self.assertFalse(ctx.exception.retryable)
        # Must not retry after a permanent error.
        self.assertEqual(self.svc.get_historical_candles.call_count, 1)

    def test_retryable_error_retries_then_succeeds(self):
        from historical_service import HistoricalFetchError
        self.svc.get_historical_candles.side_effect = [
            HistoricalFetchError("rate limit", retryable=True),
            HistoricalFetchError("rate limit", retryable=True),
            _candles(75),
        ]
        result = _fetch_candles(self.svc, "TCS", _D)
        self.assertEqual(len(result), 75)
        self.assertEqual(self.svc.get_historical_candles.call_count, 3)

    def test_retryable_error_exhausted_raises(self):
        from historical_service import HistoricalFetchError
        self.svc.get_historical_candles.side_effect = HistoricalFetchError(
            "rate limit", retryable=True
        )
        with self.assertRaises(HistoricalFetchError):
            _fetch_candles(self.svc, "TCS", _D)
        # 1 initial attempt + one per retry delay
        self.assertEqual(
            self.svc.get_historical_candles.call_count,
            1 + len(RETRY_DELAYS),
        )

    def test_interval_is_five_minute(self):
        self.svc.get_historical_candles.return_value = []
        _fetch_candles(self.svc, "TCS", _D)
        _, kw = self.svc.get_historical_candles.call_args
        self.assertEqual(kw["interval"], "FIVE_MINUTE")

    def test_exchange_is_nse(self):
        self.svc.get_historical_candles.return_value = []
        _fetch_candles(self.svc, "TCS", _D)
        _, kw = self.svc.get_historical_candles.call_args
        self.assertEqual(kw["exchange"], "NSE")

    def test_raise_on_error_is_true(self):
        """Caller must opt-in to raise_on_error so no-data vs failure is distinguishable."""
        self.svc.get_historical_candles.return_value = []
        _fetch_candles(self.svc, "TCS", _D)
        _, kw = self.svc.get_historical_candles.call_args
        self.assertTrue(kw["raise_on_error"])

    def test_from_to_dates_cover_full_session(self):
        self.svc.get_historical_candles.return_value = []
        _fetch_candles(self.svc, "TCS", _D)
        _, kw = self.svc.get_historical_candles.call_args
        self.assertEqual(kw["from_date"].hour, 9)
        self.assertEqual(kw["from_date"].minute, 15)
        self.assertEqual(kw["to_date"].hour, 15)
        self.assertEqual(kw["to_date"].minute, 30)


# ── _insert_candles ───────────────────────────────────────────────────────────

class TestInsertCandles(unittest.TestCase):
    def test_empty_input_returns_zeros(self):
        ins, ex = _insert_candles("TCS", [])
        self.assertEqual((ins, ex), (0, 0))

    def test_sql_uses_uix_candle_key_do_nothing(self):
        # Patch database.SessionLocal — where _insert_candles imports it from.
        ctx, db = _mock_session_ctx(rowcount=1)
        with patch("database.SessionLocal", return_value=ctx):
            _insert_candles("TCS", _candles(1))
        sql = db.execute.call_args[0][0].text
        self.assertIn("uix_candle_key", sql)
        self.assertIn("DO NOTHING", sql)

    def test_sql_sets_data_source_historical(self):
        ctx, db = _mock_session_ctx(rowcount=1)
        with patch("database.SessionLocal", return_value=ctx):
            _insert_candles("TCS", _candles(1))
        sql = db.execute.call_args[0][0].text
        self.assertIn("HISTORICAL", sql)

    def test_sql_sets_is_backfilled_true(self):
        ctx, db = _mock_session_ctx(rowcount=1)
        with patch("database.SessionLocal", return_value=ctx):
            _insert_candles("TCS", _candles(1))
        sql = db.execute.call_args[0][0].text
        # is_backfilled is a literal in the SQL, not a bind param
        self.assertIn("is_backfilled", sql)
        self.assertIn("true", sql.lower())

    def test_rowcount_1_counted_as_inserted(self):
        ctx, db = _mock_session_ctx(rowcount=1)
        with patch("database.SessionLocal", return_value=ctx):
            ins, ex = _insert_candles("TCS", _candles(1))
        self.assertEqual(ins, 1)
        self.assertEqual(ex, 0)

    def test_rowcount_0_counted_as_existed(self):
        ctx, db = _mock_session_ctx(rowcount=0)
        with patch("database.SessionLocal", return_value=ctx):
            ins, ex = _insert_candles("TCS", _candles(1))
        self.assertEqual(ins, 0)
        self.assertEqual(ex, 1)

    def test_timezone_aware_timestamp_stored_as_ist_naive(self):
        """
        IST-aware timestamps from the provider must be stored as IST-naive,
        matching the live aggregator so the unique constraint covers both.
        09:15+05:30 → stored 09:15 (not 03:45 UTC-naive).
        """
        ctx, db = _mock_session_ctx(rowcount=1)
        with patch("database.SessionLocal", return_value=ctx):
            _insert_candles("TCS", _candles(1))
        params = db.execute.call_args[0][1]
        stored_ts = params["timestamp"]
        # Must be naive
        self.assertIsNone(stored_ts.tzinfo)
        # IST moment preserved: 09:15, not 03:45 (UTC)
        self.assertEqual(stored_ts.hour, 9)
        self.assertEqual(stored_ts.minute, 15)


# ── prefill_due_today ─────────────────────────────────────────────────────────

class TestPrefillDueToday(unittest.TestCase):
    def _run(self, is_trading: bool, db_status: Optional[str]) -> bool:
        ctx, db = _mock_session_ctx()
        row = MagicMock()
        row.__getitem__ = lambda s, i: db_status
        db.execute.return_value.first.return_value = (
            row if db_status is not None else None
        )
        with patch("exchange_calendar.nse_calendar") as cal, \
             patch("database.get_ist_now",
                   return_value=datetime(2026, 8, 18, 17, 0)) as _gn, \
             patch("database.SessionLocal", return_value=ctx):
            cal.is_trading_day.return_value = is_trading
            return prefill_due_today()

    def test_non_trading_day_returns_false(self):
        self.assertFalse(self._run(False, None))

    def test_no_job_record_returns_true(self):
        self.assertTrue(self._run(True, None))

    def test_completed_status_returns_false(self):
        self.assertFalse(self._run(True, "COMPLETED"))

    def test_running_status_returns_true(self):
        """A RUNNING record means a previous run crashed — re-run is safe (idempotent)."""
        self.assertTrue(self._run(True, "RUNNING"))

    def test_partial_status_returns_true(self):
        self.assertTrue(self._run(True, "PARTIAL"))

    def test_failed_status_returns_true(self):
        self.assertTrue(self._run(True, "FAILED"))

    def test_weekend_returns_false(self):
        self.assertFalse(self._run(False, None))


# ── run_prefill_session ───────────────────────────────────────────────────────

class TestRunPrefillSession(unittest.TestCase):
    """Orchestration tests — all helpers are patched at the intraday_prefill level."""

    def _run(self, universe, complete, fetch_map=None, error_map=None):
        fetch_map = fetch_map or {}
        error_map = error_map or {}

        def _do_fetch(svc, ticker, session_date):
            if ticker in error_map:
                raise error_map[ticker]
            return fetch_map.get(ticker, [])

        def _do_insert(ticker, candles):
            return len(candles), 0   # all new, none existed

        # HistoricalDataService is imported locally in run_prefill_session
        # from historical_service, so patch it there.
        with patch("historical_service.HistoricalDataService") as SvcCls, \
             patch("intraday_prefill._get_universe",          return_value=list(universe)), \
             patch("intraday_prefill._get_complete_tickers",  return_value=set(complete)), \
             patch("intraday_prefill._fetch_candles",         side_effect=_do_fetch), \
             patch("intraday_prefill._insert_candles",        side_effect=_do_insert), \
             patch("intraday_prefill._upsert_job_running"), \
             patch("intraday_prefill._update_job_done"):
            SvcCls.return_value.login.return_value = True
            return run_prefill_session(_D)

    # ── Skip logic ───────────────────────────────────────────────────────────

    def test_already_complete_tickers_are_skipped(self):
        stats = self._run(
            universe=["TCS", "INFY"],
            complete={"TCS"},
            fetch_map={"INFY": _candles(75)},
        )
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["fetched"], 1)

    def test_all_complete_nothing_fetched(self):
        stats = self._run(
            universe=["A", "B"],
            complete={"A", "B"},
        )
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["fetched"], 0)
        self.assertEqual(stats["inserted"], 0)

    # ── No-data response ─────────────────────────────────────────────────────

    def test_empty_provider_response_is_no_data_not_error(self):
        """Angel One returning [] for a ticker means no data — not a failure."""
        stats = self._run(
            universe=["NEWCO"], complete=set(),
            fetch_map={"NEWCO": []},
        )
        self.assertEqual(stats["no_data"], 1)
        self.assertEqual(stats["fetched"], 0)
        self.assertEqual(stats["failed_perm"] + stats["failed_retry"], 0)
        self.assertEqual(stats["inserted"], 0)

    # ── Partial session ──────────────────────────────────────────────────────

    def test_partial_response_counted(self):
        """Provider returns > 0 but < COMPLETE_CANDLES → partial += 1."""
        stats = self._run(
            universe=["TCS"], complete=set(),
            fetch_map={"TCS": _candles(30)},
        )
        self.assertEqual(stats["fetched"], 1)
        self.assertEqual(stats["partial"], 1)
        self.assertEqual(stats["inserted"], 30)

    def test_full_session_not_counted_as_partial(self):
        stats = self._run(
            universe=["TCS"], complete=set(),
            fetch_map={"TCS": _candles(COMPLETE_CANDLES)},
        )
        self.assertEqual(stats["partial"], 0)
        self.assertEqual(stats["fetched"], 1)

    # ── Error handling ───────────────────────────────────────────────────────

    def test_permanent_error_counted_as_failed_perm(self):
        from historical_service import HistoricalFetchError
        err = HistoricalFetchError("invalid symbol", retryable=False)
        stats = self._run(
            universe=["BAD"], complete=set(),
            error_map={"BAD": err},
        )
        self.assertEqual(stats["failed_perm"], 1)
        self.assertEqual(stats["failed_retry"], 0)
        self.assertIn("BAD", stats["failed_tickers"])

    def test_retryable_exhausted_counted_as_failed_retry(self):
        from historical_service import HistoricalFetchError
        err = HistoricalFetchError("rate limit", retryable=True)
        stats = self._run(
            universe=["TCS"], complete=set(),
            error_map={"TCS": err},
        )
        self.assertEqual(stats["failed_retry"], 1)
        self.assertEqual(stats["failed_perm"], 0)
        self.assertIn("TCS", stats["failed_tickers"])

    def test_one_failure_does_not_stop_other_tickers(self):
        """A single ticker failure must not abort remaining tickers."""
        from historical_service import HistoricalFetchError
        err = HistoricalFetchError("invalid symbol", retryable=False)
        stats = self._run(
            universe=["BAD", "GOOD"],
            complete=set(),
            fetch_map={"GOOD": _candles(75)},
            error_map={"BAD": err},
        )
        self.assertEqual(stats["fetched"], 1)
        self.assertEqual(stats["failed_perm"], 1)
        self.assertEqual(stats["inserted"], 75)

    # ── DO NOTHING / overwrite prevention ────────────────────────────────────

    def test_do_nothing_all_existed_zero_inserted(self):
        """When _insert_candles returns (0, n) the prefill doesn't overwrite anything."""
        with patch("historical_service.HistoricalDataService") as SvcCls, \
             patch("intraday_prefill._get_universe",          return_value=["TCS"]), \
             patch("intraday_prefill._get_complete_tickers",  return_value=set()), \
             patch("intraday_prefill._fetch_candles",         return_value=_candles(75)), \
             patch("intraday_prefill._insert_candles",        return_value=(0, 75)), \
             patch("intraday_prefill._upsert_job_running"), \
             patch("intraday_prefill._update_job_done"):
            SvcCls.return_value.login.return_value = True
            stats = run_prefill_session(_D)

        self.assertEqual(stats["inserted"], 0)
        self.assertEqual(stats["existed"], 75)
        # fetched counts API calls that returned data, regardless of DB outcome
        self.assertEqual(stats["fetched"], 1)

    # ── Restart / resume idempotency ─────────────────────────────────────────

    def test_restart_is_idempotent(self):
        """Calling run_prefill_session twice: first run inserts, second gets DO NOTHING."""
        call_n = {"v": 0}

        def _insert(ticker, candles):
            call_n["v"] += 1
            return (75, 0) if call_n["v"] == 1 else (0, 75)

        with patch("historical_service.HistoricalDataService") as SvcCls, \
             patch("intraday_prefill._get_universe",          return_value=["TCS"]), \
             patch("intraday_prefill._get_complete_tickers",  return_value=set()), \
             patch("intraday_prefill._fetch_candles",         return_value=_candles(75)), \
             patch("intraday_prefill._insert_candles",        side_effect=_insert), \
             patch("intraday_prefill._upsert_job_running"), \
             patch("intraday_prefill._update_job_done"):
            SvcCls.return_value.login.return_value = True
            s1 = run_prefill_session(_D)
            s2 = run_prefill_session(_D)

        self.assertEqual(s1["inserted"], 75)
        self.assertEqual(s1["existed"],  0)
        self.assertEqual(s2["inserted"], 0)
        self.assertEqual(s2["existed"],  75)

    # ── Login failure ────────────────────────────────────────────────────────

    def test_login_failure_raises_runtime_error(self):
        """If AngelOne login fails the function must raise, not silently skip."""
        with patch("historical_service.HistoricalDataService") as SvcCls, \
             patch("intraday_prefill._upsert_job_running"):
            SvcCls.return_value.login.return_value = False
            with self.assertRaises(RuntimeError):
                run_prefill_session(_D)

    # ── Stats accounting ─────────────────────────────────────────────────────

    def test_stats_totals_are_consistent(self):
        """total == skipped + fetched + no_data + failed_perm + failed_retry."""
        from historical_service import HistoricalFetchError
        stats = self._run(
            universe=["A", "B", "C", "D"],
            complete={"A"},
            fetch_map={"B": _candles(75), "C": []},
            error_map={"D": HistoricalFetchError("not found", retryable=False)},
        )
        self.assertEqual(stats["total"], 4)
        accounted = (
            stats["skipped"] +
            stats["fetched"] +
            stats["no_data"] +
            stats["failed_perm"] +
            stats["failed_retry"]
        )
        self.assertEqual(accounted, stats["total"])


if __name__ == "__main__":
    unittest.main()
