"""Targeted regressions for session-aware intraday gap detection and repair.

Covers: holidays, overnight sessions, full-session gaps, older intraday gaps,
scattered 1-5 bucket gaps, duplicate-job prevention, retry/terminal outcomes,
pre-listing handling, and the frontend repair signal.

No provider calls and no database: the DB-facing seams are patched, so these run
anywhere (the production schema is Postgres-only in places).
"""
import datetime
import inspect
import os
import unittest
from datetime import datetime as dt, timedelta
from unittest import mock

import backfill_jobs as bj
import main

HOLIDAYS = {datetime.date(2026, 10, 2), datetime.date(2026, 9, 14)}


class _StubDb:
    """Returns pre-seeded (cur, prev) rows for the lag query."""

    def __init__(self, pairs):
        self._pairs = pairs

    def execute(self, *_a, **_k):
        pairs = self._pairs

        class _R:
            def fetchall(self_inner):
                return pairs
        return _R()


class _FakeSession:
    def close(self):
        pass


class SessionAwareDetectionTests(unittest.TestCase):
    """The calendar must decide, not a raw day count."""

    def setUp(self):
        self._saved = set(main.NSE_HOLIDAYS)
        main.NSE_HOLIDAYS.clear()
        main.NSE_HOLIDAYS.update(HOLIDAYS)

    def tearDown(self):
        main.NSE_HOLIDAYS.clear()
        main.NSE_HOLIDAYS.update(self._saved)

    def test_holiday_extended_weekend_is_not_a_gap(self):
        # Thu 1 Oct 15:25 -> Mon 5 Oct 09:15. 2 Oct = Gandhi Jayanti, 3-4 Oct
        # weekend. The old raw >3-day test called this a hole and enqueued a job
        # for nearly every ticker; the calendar says nothing is missing.
        n = main._absent_session_buckets(dt(2026, 10, 1, 15, 25), dt(2026, 10, 5, 9, 15))
        self.assertEqual(n, 0)

    def test_normal_overnight_is_not_a_gap(self):
        n = main._absent_session_buckets(dt(2026, 10, 5, 15, 25), dt(2026, 10, 6, 9, 15))
        self.assertEqual(n, 0)

    def test_ordinary_weekend_is_not_a_gap(self):
        # Fri 9 Oct 15:25 -> Mon 12 Oct 09:15
        n = main._absent_session_buckets(dt(2026, 10, 9, 15, 25), dt(2026, 10, 12, 9, 15))
        self.assertEqual(n, 0)

    def test_whole_missing_session_counts_75(self):
        n = main._absent_session_buckets(dt(2026, 10, 5, 15, 25), dt(2026, 10, 7, 9, 15))
        self.assertEqual(n, 75)

    def test_multi_day_gap_counts_every_session(self):
        # 5 Oct close -> 9 Oct open: 6, 7, 8 Oct are fully missing.
        n = main._absent_session_buckets(dt(2026, 10, 5, 15, 25), dt(2026, 10, 9, 9, 15))
        self.assertEqual(n, 225)

    def test_thirty_minute_intraday_hole_is_repairable(self):
        n = main._absent_session_buckets(dt(2026, 10, 6, 10, 0), dt(2026, 10, 6, 10, 35))
        self.assertEqual(n, 6)
        self.assertGreaterEqual(n, main._GAP_MIN_REPAIRABLE_BUCKETS)

    def test_scattered_one_to_five_buckets_are_below_the_sweep_threshold(self):
        n = main._absent_session_buckets(dt(2026, 10, 6, 10, 0), dt(2026, 10, 6, 10, 15))
        self.assertEqual(n, 2)
        self.assertLess(n, main._GAP_MIN_REPAIRABLE_BUCKETS)


class DetectorThresholdTests(unittest.TestCase):
    def setUp(self):
        self._saved = set(main.NSE_HOLIDAYS)
        main.NSE_HOLIDAYS.clear()
        main.NSE_HOLIDAYS.update(HOLIDAYS)

    def tearDown(self):
        main.NSE_HOLIDAYS.clear()
        main.NSE_HOLIDAYS.update(self._saved)

    def test_holiday_only_window_is_not_a_hole(self):
        db = _StubDb([(dt(2026, 10, 5, 9, 15), dt(2026, 10, 1, 15, 25))])
        self.assertFalse(main._has_multiday_intraday_hole(db, "X", "5m", dt(2026, 8, 1)))

    def test_genuine_multi_day_gap_is_a_hole(self):
        db = _StubDb([(dt(2026, 10, 9, 9, 15), dt(2026, 10, 5, 15, 25))])
        self.assertTrue(main._has_multiday_intraday_hole(db, "X", "5m", dt(2026, 8, 1)))

    def test_scattered_small_gap_is_below_the_sweep_threshold(self):
        # 2 consecutive buckets missing (an illiquid no-trade window).
        db = _StubDb([(dt(2026, 10, 6, 10, 15), dt(2026, 10, 6, 10, 0))])
        self.assertFalse(main._has_multiday_intraday_hole(db, "X", "5m", dt(2026, 8, 1)),
                         "the universe sweep must not chase a 2-bucket hole")

    def test_partially_missing_morning_session_is_seen_by_the_sweep(self):
        # 09:15-11:00 missing: prev session close 15:25 -> next bar 11:05 is 19h40m,
        # which a 20h pre-filter would have missed entirely.
        db = _StubDb([(dt(2026, 10, 6, 11, 5), dt(2026, 10, 5, 15, 25))])
        self.assertTrue(main._has_multiday_intraday_hole(db, "X", "5m", dt(2026, 8, 1)))

    def test_older_intraday_gap_is_detected(self):
        # A 40-minute hole 3 months back must still be found (the chart-open
        # internal check only looks 5 days back).
        db = _StubDb([(dt(2026, 7, 15, 11, 40), dt(2026, 7, 15, 11, 0))])
        self.assertTrue(main._has_multiday_intraday_hole(db, "X", "5m", dt(2026, 1, 1)))

    def test_query_failure_is_treated_as_no_hole(self):
        class Boom:
            def execute(self, *a, **k):
                raise RuntimeError("no such table")
        self.assertFalse(main._has_multiday_intraday_hole(Boom(), "X", "5m", dt(2026, 8, 1)))


class StableJobRangeTests(unittest.TestCase):
    def test_range_is_stable_across_seconds(self):
        a = main._stable_job_range("5m", dt(2026, 10, 13, 18, 10, 8))
        b = main._stable_job_range("5m", dt(2026, 10, 13, 18, 39, 43))
        self.assertEqual(a, b, "a now-derived key caused a duplicate job row per sweep pass")

    def test_range_is_day_aligned(self):
        s, e = main._stable_job_range("5m", dt(2026, 10, 13, 18, 10, 8))
        self.assertEqual((s.hour, s.minute, s.second), (0, 0, 0))
        self.assertEqual(e.date(), datetime.date(2026, 10, 13))
        self.assertEqual(e.hour, 23)

    def test_different_intervals_get_different_keys(self):
        self.assertNotEqual(main._stable_job_range("5m", dt(2026, 10, 13, 12, 0)),
                            main._stable_job_range("15m", dt(2026, 10, 13, 12, 0)))


class DuplicateJobTests(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(bj)

    def test_conflict_target_matches_the_partial_unique_index(self):
        i = self.src.find("def enqueue(")
        block = self.src[i:i + 1400]
        self.assertIn("ON CONFLICT (ticker, exchange, interval, range_start, range_end)", block)
        self.assertIn("uix_backfill_jobs_open", self.src)

    def test_enqueue_is_idempotent_and_returns_the_existing_row(self):
        i = self.src.find("def enqueue(")
        block = self.src[i:i + 1400]
        self.assertIn("DO UPDATE SET updated_at = NOW()", block)

    def test_missing_ranges_excludes_recorded_absent_buckets(self):
        day = datetime.date(2026, 10, 6)
        expected = bj.session_buckets(day)
        absent_bucket = expected[3]
        db = mock.MagicMock()
        with mock.patch.object(bj, "_stored_buckets", return_value=set()), \
             mock.patch.object(bj, "_absent_buckets", return_value={absent_bucket}):
            missing, _ = bj.missing_ranges(db, "X", "5m", dt(2026, 10, 6, 9, 15),
                                           dt(2026, 10, 6, 15, 25),
                                           now=dt(2026, 10, 7, 12, 0))
        self.assertNotIn(absent_bucket, missing, "a proven-absent bucket must not be re-fetched")
        self.assertIn(expected[4], missing)

    def test_has_missing_gates_job_creation(self):
        db = mock.MagicMock()
        with mock.patch.object(bj, "missing_ranges", return_value=({dt(2026, 10, 6, 10, 0)}, [])):
            self.assertTrue(bj.has_missing(db, "X", "5m", dt(2026, 8, 1), dt(2026, 10, 13)))
        with mock.patch.object(bj, "missing_ranges", return_value=(set(), [])):
            self.assertFalse(bj.has_missing(db, "X", "5m", dt(2026, 8, 1), dt(2026, 10, 13)))


class RunJobOutcomeTests(unittest.TestCase):
    """Terminal outcomes must be evidence-based, never guessed."""

    MISSING = {dt(2026, 10, 6, 10, 0)}
    RANGES = [(dt(2026, 8, 1), dt(2026, 10, 13))]

    def setUp(self):
        self.finish = mock.patch.object(bj, "finish").start()
        self.mark = mock.patch.object(bj, "mark_absent", return_value=1).start()
        mock.patch.object(bj.database, "SessionLocal", lambda: _FakeSession()).start()
        mock.patch.object(bj, "_pre_data_floor", return_value=None).start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, missing_after, fetch=None, raises=None, pre_floor=None,
             attempts=1, missing=None, rate_limited=False, auth=False):
        """missing_after = still-missing set re-read after persistence.
        `raises` makes the provider call fail; otherwise `fetch` is returned."""
        first = set(self.MISSING) if missing is None else set(missing)
        seq = [(first, self.RANGES), (missing_after, [])]
        with mock.patch.object(bj, "missing_ranges", side_effect=seq), \
             mock.patch.object(bj, "_pre_data_floor", return_value=pre_floor), \
             mock.patch("aggregator.batch_flush_candles"), \
             mock.patch("time.sleep"), \
             mock.patch("historical_service.rate_limit_backoff_remaining", return_value=0.0), \
             mock.patch("historical_service._looks_rate_limited", return_value=rate_limited), \
             mock.patch("historical_service._looks_auth_error", return_value=auth), \
             mock.patch("historical_service._retry_after_seconds", return_value=0.0):
            svc = mock.MagicMock()
            svc.is_logged_in = True
            if raises is not None:
                svc.get_historical_candles.side_effect = raises
            else:
                svc.get_historical_candles.return_value = [] if fetch is None else fetch
            with mock.patch("historical_service.historical_service", svc):
                status, detail = bj.run_job({
                    "id": 1, "ticker": "X", "exchange": "NSE", "interval": "5m",
                    "range_start": dt(2026, 8, 1), "range_end": dt(2026, 10, 13),
                    "attempts": attempts})
        return status, detail, svc

    def _finish_status(self):
        return self.finish.call_args[0][1]

    def _finish_kind(self):
        return self.finish.call_args[1].get("failure_kind")

    def test_unsupported_symbol_fails_immediately(self):
        from historical_service import HistoricalFetchError
        err = HistoricalFetchError("Ticker 'AAPLUSTRAD' not found", retryable=False)
        status, _d, svc = self._run(set(self.MISSING), raises=err)
        self.assertEqual(status, "permanently_failed")
        self.assertEqual(self._finish_kind(), "unsupported")
        self.assertEqual(svc.get_historical_candles.call_count, 1,
                         "an invalid symbol must not be retried 6 times")

    def test_empty_responses_alone_never_become_provider_absent(self):
        # Successful calls that simply returned nothing -> NOT proven absent.
        status, _d, svc = self._run(set(self.MISSING), fetch=[])
        self.assertEqual(status, "retry_wait")
        self.assertEqual(self._finish_kind(), "empty_response")
        self.assertFalse(self.mark.called, "absence must not be inferred from empty responses")
        self.assertEqual(svc.get_historical_candles.call_count, 1)

    def test_throttling_is_retry_wait(self):
        from historical_service import HistoricalFetchError
        status, _d, _s = self._run(
            set(self.MISSING), rate_limited=True,
            raises=HistoricalFetchError("Access denied because of exceeding access rate"))
        self.assertEqual(status, "retry_wait")
        self.assertEqual(self._finish_kind(), "throttled")

    def test_transient_error_is_retry_wait(self):
        status, _d, _s = self._run(set(self.MISSING), raises=RuntimeError("connection reset"))
        self.assertEqual(status, "retry_wait")
        self.assertEqual(self._finish_kind(), "transient")

    def test_partial_response_is_partially_complete(self):
        b1, b2 = dt(2026, 10, 6, 10, 0), dt(2026, 10, 6, 10, 5)
        candles = [{"timestamp": dt(2026, 10, 6, 10, 30), "open": 1.0, "high": 1.0,
                    "low": 1.0, "close": 1.0, "volume": 5}]
        status, detail, _s = self._run({b2}, fetch=candles, missing={b1, b2})
        self.assertEqual(status, "partially_complete")
        self.assertEqual(detail["persisted"], 1)

    def test_bracketed_omission_is_provider_absent(self):
        # The response returned data BEFORE and AFTER the missing bucket but not
        # the bucket itself -> proven absent, terminal, recorded.
        candles = [{"timestamp": dt(2026, 10, 6, 9, 45), "open": 1.0, "high": 1.0,
                    "low": 1.0, "close": 1.0, "volume": 1},
                   {"timestamp": dt(2026, 10, 6, 10, 30), "open": 1.0, "high": 1.0,
                    "low": 1.0, "close": 1.0, "volume": 1}]
        status, detail, _s = self._run(set(self.MISSING), fetch=candles)
        self.assertEqual(status, "complete_with_gaps")
        self.assertEqual(self._finish_kind(), "provider_absent")
        self.assertEqual(self.mark.call_args[0][4], "provider_absent")
        self.assertEqual(detail["provider_absent"], 1)

    def test_unbracketed_omission_is_not_provider_absent(self):
        # Data only AFTER the bucket: a truncated range would look identical, so
        # this must stay unresolved rather than being declared absent.
        candles = [{"timestamp": dt(2026, 10, 6, 11, 0), "open": 1.0, "high": 1.0,
                    "low": 1.0, "close": 1.0, "volume": 1}]
        status, _d, _s = self._run(set(self.MISSING), fetch=candles)
        self.assertEqual(status, "retry_wait")
        self.assertFalse(self.mark.called)

    def test_all_pre_data_is_labelled_pre_data_not_provider_absent(self):
        floor = dt(2026, 10, 20)          # everything missing predates the floor
        status, detail, svc = self._run(set(self.MISSING), fetch=[], pre_floor=floor)
        self.assertEqual(status, "complete_with_gaps")
        self.assertEqual(self._finish_kind(), "pre_data")
        self.assertEqual(self.mark.call_args[0][4], "pre_data")
        self.assertEqual(svc.get_historical_candles.call_count, 0,
                         "a purely pre-listing window must not spend a request")

    def test_complete_only_when_nothing_is_missing(self):
        status, detail, _s = self._run(set(), fetch=[])
        self.assertEqual(status, "complete")
        self.assertEqual(self._finish_status(), "complete")
        self.assertEqual(detail["persisted"], len(self.MISSING))

    def test_exhausted_attempts_end_honestly_not_permanently(self):
        # The provider ANSWERED SUCCESSFULLY but had no candle for the buckets and
        # the attempts are spent -> terminal complete_with_gaps / provider_empty.
        # Deliberately not provider_absent (that needs bracketing evidence) and not
        # permanently_failed (the symbol is fine, the data is simply absent).
        status, _d, _s = self._run(set(self.MISSING), fetch=[], attempts=bj.JOB_MAX_ATTEMPTS)
        self.assertEqual(status, "complete_with_gaps")
        self.assertEqual(self._finish_kind(), "provider_empty")

    def test_genuine_error_at_max_attempts_still_fails_permanently(self):
        status, _d, _s = self._run(set(self.MISSING), raises=RuntimeError("boom"),
                                   attempts=bj.JOB_MAX_ATTEMPTS)
        self.assertEqual(status, "permanently_failed")

    def test_auth_failure_at_max_attempts_still_retries(self):
        # 180 valid jobs were permanently failed by 6 attempts of "Invalid Token".
        # An auth error must refresh the session and retry, never go permanent.
        from historical_service import HistoricalFetchError
        err = HistoricalFetchError("Invalid Token")
        status, _d, _s = self._run(set(self.MISSING), raises=err, auth=True,
                                   attempts=bj.JOB_MAX_ATTEMPTS)
        self.assertEqual(status, "retry_wait")
        self.assertEqual(self._finish_kind(), "auth")

    def test_tz_aware_provider_timestamps_do_not_break_bracketing(self):
        # Angel One returns "+05:30" timestamps. Comparing those against the naive
        # bucket times raised TypeError, so provider_absent was never reachable and
        # the job just logged an internal error forever.
        off = datetime.timezone(timedelta(hours=5, minutes=30))
        candles = [{"timestamp": dt(2026, 10, 6, 9, 45, tzinfo=off), "open": 1.0, "high": 1.0,
                    "low": 1.0, "close": 1.0, "volume": 1},
                   {"timestamp": dt(2026, 10, 6, 10, 30, tzinfo=off), "open": 1.0, "high": 1.0,
                    "low": 1.0, "close": 1.0, "volume": 1}]
        status, detail, _s = self._run(set(self.MISSING), fetch=candles)
        self.assertEqual(status, "complete_with_gaps")
        self.assertEqual(detail["provider_absent"], 1)


class IstNaiveTests(unittest.TestCase):
    def test_ist_naive_normalises_offsets(self):
        off = datetime.timezone(timedelta(hours=5, minutes=30))
        self.assertEqual(bj._ist_naive(dt(2026, 10, 6, 9, 45, tzinfo=off)),
                         dt(2026, 10, 6, 9, 45))
        # a UTC-stamped value must land on the IST wall clock (04:15Z == 09:45 IST)
        utc = dt(2026, 10, 6, 4, 15, tzinfo=datetime.timezone.utc)
        self.assertEqual(bj._ist_naive(utc), dt(2026, 10, 6, 9, 45))
        naive = dt(2026, 10, 6, 9, 45)
        self.assertEqual(bj._ist_naive(naive), naive)
        self.assertIsNone(bj._ist_naive(naive).tzinfo)


class StatusContractTests(unittest.TestCase):
    """Requirement: verify statuses against the real schema and queries."""

    def setUp(self):
        self.src = inspect.getsource(bj)

    def test_status_values_fit_the_column(self):
        for s in ("complete", "complete_with_gaps", "permanently_failed",
                  "retry_wait", "partially_complete", "queued", "running"):
            self.assertLessEqual(len(s), 20, "%s exceeds VARCHAR(20)" % s)

    def test_claim_query_excludes_terminal_statuses(self):
        i = self.src.find("def claim(")
        block = self.src[i:i + 1800]
        self.assertIn("status = 'queued'", block)
        self.assertIn("status = 'retry_wait'", block)
        self.assertIn("status = 'partially_complete'", block)
        self.assertNotIn("complete_with_gaps", block, "a terminal job must never be re-claimed")
        self.assertNotIn("'complete'", block)

    def test_retry_backoff_only_applies_to_retryable_states(self):
        i = self.src.find("def finish(")
        block = self.src[i:i + 900]
        self.assertIn('if status in ("retry_wait", "partially_complete")', block)

    def test_retention_includes_the_new_terminal_status(self):
        i = self.src.find("def cleanup(")
        self.assertIn("complete_with_gaps", self.src[i:i + 700])

    def test_absent_reasons_are_distinct(self):
        for reason in ("provider_absent", "pre_data"):
            self.assertIn('"%s"' % reason, self.src)

    def test_migration_file_matches_the_runtime_ddl(self):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "v009_backfill_absent.sql")
        self.assertTrue(os.path.exists(p), "v009 migration missing")
        with open(p) as f:
            sql = f.read()
        self.assertIn("CREATE TABLE IF NOT EXISTS backfill_absent", sql)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uix_backfill_absent", sql)
        self.assertIn("backfill_absent", self.src, "runtime DDL must create it too")


class FrontendRepairSignalTests(unittest.TestCase):
    def _main_src(self):
        return inspect.getsource(main.get_intraday_paginated)

    def test_backfill_status_covers_a_queued_job(self):
        src = self._main_src()
        self.assertIn("job_queued", src)
        self.assertIn("has_open_job", src)
        self.assertIn('bf_status = "backfilling"', src)

    def test_chart_open_uses_the_exact_missing_check(self):
        src = self._main_src()
        self.assertIn("has_missing", src)
        self.assertIn("_stable_job_range", src)
        # The session-scale heuristic must NOT gate on-demand repair: its SQL
        # pre-filter only sees jumps > 12h, so a scattered intraday hole would be
        # silently ignored. The exact missing-set check is used instead.
        self.assertNotIn("min_buckets=1", src)
        self.assertNotIn("_has_multiday_intraday_hole", src,
                         "on-demand repair must not depend on the session-scale detector")

    def test_sweep_still_uses_the_session_scale_threshold(self):
        src = inspect.getsource(main._sweep_intraday_gaps_once)
        self.assertIn("_has_multiday_intraday_hole(db, t, '5m', floor)", src)
        self.assertNotIn("min_buckets=1", src,
                         "the universe sweep must stay at the 30-minute threshold")

    def test_frontend_polling_stays_bounded_and_merges(self):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "frontend", "dashboard.js")
        if not os.path.exists(p):
            self.skipTest("frontend not present")
        with open(p, encoding="utf-8", errors="ignore") as f:
            js = f.read()
        self.assertIn("backfill_status", js)
        self.assertIn("_bgPollCount < 2", js, "background polling must stay bounded")
        self.assertIn("_renderChartData(refData, range)", js, "must merge, not reset")


class DerivedTimeframeTests(unittest.TestCase):
    """5m is the only intraday source of truth; 15m/30m/1h/4h are derived on the
    fly from stored 5m and are never fetched from the provider nor persisted."""

    def test_four_hour_is_a_supported_derived_timeframe(self):
        self.assertIn("4h", main.INTRADAY_TFS)
        self.assertIn("4h", main.DERIVED_FROM_5M)
        self.assertIsNotNone(main._intraday_cutoff("4h"))

    def test_resampler_supports_4h_aligned_to_the_0915_open(self):
        from resampler import CandleResampler
        self.assertIn("4h", CandleResampler.RESAMPLE_RULES)
        rows = []
        for k in range(75):
            ts = dt(2026, 10, 6, 9, 15) + timedelta(minutes=5 * k)
            rows.append({"timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.5, "volume": 10})
        out = CandleResampler.resample_5m_to(rows, "4h")
        self.assertTrue(out, "4h resampling produced nothing")
        first = out[0]["timestamp"]
        self.assertEqual((first.hour, first.minute), (9, 15),
                         "4h buckets must start at the 09:15 session open (offset='75min')")

    def test_derived_timeframe_job_is_superseded_without_a_provider_call(self):
        with mock.patch.object(bj, "finish") as fin:
            status, detail = bj.run_job({"id": 9, "ticker": "X", "exchange": "NSE",
                                         "interval": "15m",
                                         "range_start": dt(2026, 8, 1),
                                         "range_end": dt(2026, 10, 13), "attempts": 1})
        self.assertEqual(status, "superseded")
        self.assertEqual(detail["derived_from"], "5m")
        self.assertEqual(fin.call_args[0][1], "superseded")
        self.assertEqual(fin.call_args[1]["failure_kind"], "derived_from_5m")

    def test_5m_job_is_not_superseded(self):
        src = inspect.getsource(bj.run_job)
        i = src.find('if job["interval"] != "5m":')
        self.assertGreater(i, -1, "5m must be the only provider-fetched intraday TF")
        self.assertIn("superseded", src[i:i + 400])

    def test_superseded_is_terminal_and_retained(self):
        src = inspect.getsource(bj)
        claim = src[src.find("def claim("):src.find("def claim(") + 1800]
        self.assertNotIn("superseded", claim, "a superseded job must never be re-claimed")
        cleanup = src[src.find("def cleanup("):src.find("def cleanup(") + 700]
        self.assertIn("superseded", cleanup)

    def test_chart_open_enqueues_only_the_5m_job(self):
        src = inspect.getsource(main.get_intraday_paginated)
        self.assertIn('_stable_job_range("5m", now)', src)
        self.assertIn('_bj.enqueue(clean_ticker, "5m"', src)
        self.assertNotIn("_stable_job_range(interval, now)", src,
                         "a per-timeframe job would mean a per-timeframe provider call")

    def test_derived_timeframes_never_persist_or_call_the_provider(self):
        src = inspect.getsource(main.perform_on_demand_backfill)
        i = src.find("derived from 5m ")
        self.assertGreater(i, -1, "derived timeframes must short-circuit")
        self.assertIn("return 0", src[i:i + 400],
                      "a derived timeframe must return without persisting anything")
        self.assertGreater(src.find("if not intraday_candles:"), i,
                           "the short-circuit must come BEFORE the provider fetch")

    def test_backfill_jobs_reject_a_derived_interval(self):
        self.assertNotIn("4h", bj.MAX_SPAN_DAYS,
                         "4h must not be enqueueable: it is derived, not fetched")


class AuthErrorTests(unittest.TestCase):
    """'Invalid Token' must refresh the session and retry -- never kill a job."""

    def test_auth_errors_are_recognised(self):
        import historical_service as hs
        for m in ("Invalid Token", "Session expired", "Unauthorized", "JWT expired"):
            self.assertTrue(hs._looks_auth_error(m), m)

    def test_throttle_and_bad_symbol_are_not_auth_errors(self):
        import historical_service as hs
        self.assertFalse(hs._looks_auth_error("Access denied because of exceeding access rate"),
                         "a throttle must not be misread as an auth failure")
        self.assertFalse(hs._looks_auth_error("Ticker 'XYZ' not found"))
        self.assertFalse(hs._looks_auth_error("connection reset"))

    def test_worker_retries_auth_inline_and_never_fails_permanently(self):
        src = inspect.getsource(bj.run_job)
        self.assertIn('kind = "auth"', src)
        self.assertIn("_looks_auth_error", src)
        self.assertIn('kind != "auth"', src,
                      "an auth error must never reach permanently_failed")

    def test_derived_timeframes_ignore_stored_rows(self):
        """Derived TFs must not merge the legacy stored rows into the LATEST
        window: merging them there left permanent holes (stored 15m is a subset
        of what 5m produces).

        The discard is now scoped to `is_latest_request`. It used to be
        unconditional, which silently broke the other half of the contract:
        a scroll-back page (`before=...`) returns zero rows and has_more=false,
        so the chart could never load 15m/30m/1h history older than
        `_intraday_cutoff()` even though the retained stored rows were sitting
        in the DB (measured 2026-10-18: ETERNAL 15m had 3476 stored rows back to
        March, every scroll-back returned 0). The history-page contract is
        covered by tests/test_derived_tf_history_page.py.
        """
        src = inspect.getsource(main.get_intraday_paginated)
        self.assertIn("DERIVED TIMEFRAMES", src, "the derived-TF branch must exist")
        i = src.find("if interval in DERIVED_FROM_5M and is_latest_request:")
        self.assertGreater(i, -1,
                           "the derived-TF discard must be scoped to the latest window")
        self.assertIn("records = []", src[i:i + 200],
                      "a derived timeframe must not merge legacy stored rows into "
                      "the latest window")
        self.assertNotIn("if interval in DERIVED_FROM_5M:\n        records = []", src,
                         "the unconditional discard broke lazy-load and must not return")

    def test_sweep_only_enqueues_real_active_nse_instruments(self):
        src = inspect.getsource(main._sweep_intraday_gaps_once)
        self.assertIn("exchange = 'NSE'", src)
        self.assertIn("is_active", src)
        self.assertIn("~ '^[A-Z]'", src, "fund units / NCD codes like 11AGG must be excluded")
        self.assertNotIn("WHERE ticker IS NOT NULL", src,
                         "the unfiltered sweep enqueued BSE-only and inactive symbols")

    def test_user_repair_is_not_refused_by_the_shared_cooldown(self):
        # so the visitor saw a gap. A user-requested bounded repair must not
        # pre-yield to the background cooldown.
        src = inspect.getsource(main._sync_repair_recent)
        self.assertIn("priority=True", src,
                      "the on-open repair must be a priority call")
        import historical_service as hs
        import inspect as _i
        gsrc = _i.getsource(hs.HistoricalDataService.get_historical_candles)
        self.assertIn("if not priority:", gsrc,
                      "the shared cooldown must be bypassable for a priority caller")
        self.assertIn("priority: bool = False", gsrc)
        # There are TWO gates: get_historical_candles ("shared cooldown") and
        # _fetch_historical_uncached ("global backoff"). Both must honour priority,
        # otherwise the repair is still refused -- which is exactly what happened
        # on the first attempt (CUPID logged "global backoff, 72s left").
        fsrc = _i.getsource(hs.HistoricalDataService._fetch_historical_uncached)
        self.assertIn("if _rl_left > 0 and not priority:", fsrc,
                      "the global-backoff gate must also honour priority")
        self.assertIn("priority: bool = False", fsrc)


class OutOfOrderTickTests(unittest.TestCase):
    """An out-of-order tick inside the CURRENT bucket must widen the range."""

    def test_out_of_order_tick_widens_range_but_not_close(self):
        import aggregator as agg
        src = inspect.getsource(type(agg.candle_aggregator).process_tick)
        i = src.find("last_ts is not None and tick_ts < last_ts")
        self.assertGreater(i, -1, "the monotonic guard must still exist")
        self.assertNotIn("return {}", src[i:i + 500],
                         "dropping the tick narrowed the live candle's high/low")
        self.assertIn("_out_of_order = True", src[i:i + 500])
        self.assertIn("if not _out_of_order:", src,
                      "a late tick must not pull the close backwards")
        self.assertIn("forming[\"high\"] = max(forming[\"high\"], price)", src)
        self.assertIn("forming[\"low\"] = min(forming[\"low\"], price)", src)


class QuotaRebalanceTests(unittest.TestCase):
    """Background consumers must not keep the account permanently throttled.

    Measured on production: intraday prefill retried NIFTY/BANKNIFTY 5s/15s/30s
    apart *during* the 90s shared cooldown, and the dashboard-index backfill walked
    every remaining day/index the same way, so the account stayed throttled and the
    USER's own repair (CUPID) was refused by Angel One with "Access denied because
    of exceeding access rate".
    """

    def test_account_pacing_is_slower(self):
        import historical_service as hs
        self.assertGreaterEqual(hs._HISTORICAL_MIN_INTERVAL_SEC, 4.0,
                                "2.0s pacing kept the account continuously throttled")

    def test_prefill_does_not_retry_a_rate_limit(self):
        import inspect as _i
        import intraday_prefill as ip
        src = _i.getsource(ip)
        i = src.find("if _is_rate_limited(exc):")
        self.assertGreater(i, -1, "a rate limit must abort the retry loop")
        self.assertIn("break", src[i:i + 60])

    def test_index_backfill_stops_on_rate_limit(self):
        import inspect as _i
        src = _i.getsource(main)
        i = src.find("rate limited - stopping this batch")
        self.assertGreater(i, -1, "the index backfill must stop its batch when throttled")
        self.assertIn("return", src[i:i + 200])

    def test_priority_repairs_are_capped(self):
        # An unbounded priority exemption keeps the account throttled (every refused
        # call extends it), so repairs are capped account-wide.
        import historical_service as hs
        self.assertGreaterEqual(hs._HISTORICAL_PRIORITY_MIN_INTERVAL_SEC, 10.0)
        hs._priority_next_slot = 0.0
        self.assertTrue(hs._acquire_priority_slot())
        self.assertFalse(hs._acquire_priority_slot(), "a second repair must wait its turn")
        hs._priority_next_slot = 0.0


if __name__ == "__main__":
    unittest.main()
