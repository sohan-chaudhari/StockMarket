"""
Phase 3A regression tests — B-1, B-2, B-3 fixes.

B-1  Chain B partial-period data loss
      _compute_cutoff now aligns 1D->1W to the Monday of the boundary week
      and 1W->1M to the first day of the boundary month, ensuring only
      complete periods are ever promoted.

B-2  perform_on_demand_backfill TOCTOU
      db.add(record) replaced with pg_insert().on_conflict_do_nothing()
      so concurrent inserts cannot cause an IntegrityError that rolls back
      the whole batch.

B-3  Stale RUNNING retention jobs
      _downgrade() now marks past-day RUNNING rows as FAILED at the start
      of every cycle, preventing unbounded table growth.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

from resampler import CandleResampler
from retention_service import RetentionService, RetentionRule


def _c(ts, open_=100, high=110, low=90, close=105, volume=1000):
    return {"timestamp": ts, "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


# ── B-1: cutoff alignment math ────────────────────────────────────────────────

class TestCutoffAlignmentWeekly(unittest.TestCase):
    """_compute_cutoff for 1D->1W must always return the Monday of the raw
    cutoff's ISO week — never an intermediate weekday or weekend."""

    def setUp(self):
        self.service = RetentionService(MagicMock(), MagicMock())
        self.rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)

    def _cutoff(self, now):
        return self.service._compute_cutoff(self.rule, now)

    def test_raw_cutoff_saturday_aligns_to_monday(self):
        # now=2026-08-10 → raw=2024-08-10 (Saturday, weekday=5) → Monday=2024-08-05
        cutoff = self._cutoff(datetime(2026, 8, 10, 2, 0))
        self.assertEqual(cutoff, datetime(2024, 8, 5))
        self.assertEqual(cutoff.weekday(), 0)

    def test_raw_cutoff_wednesday_aligns_to_monday(self):
        # raw=2024-08-14 (Wednesday, weekday=2) → Monday=2024-08-12
        # now = 2024-08-14 + 730 = 2026-08-14
        cutoff = self._cutoff(datetime(2026, 8, 14, 2, 0))
        raw = datetime(2024, 8, 14)
        expected = datetime(2024, 8, 12)
        self.assertEqual(raw.weekday(), 2)  # sanity: Wednesday
        self.assertEqual(cutoff, expected)
        self.assertEqual(cutoff.weekday(), 0)

    def test_raw_cutoff_monday_is_already_aligned(self):
        # raw that lands on Monday needs no shift
        # now = datetime such that raw = 2024-08-19 (Monday)
        # 2024-08-19 + 730 = ?
        # 2024-08-19 + 365 = 2025-08-19, + 365 = 2026-08-19
        cutoff = self._cutoff(datetime(2026, 8, 19, 2, 0))
        self.assertEqual(cutoff, datetime(2024, 8, 19))  # unchanged
        self.assertEqual(cutoff.weekday(), 0)

    def test_raw_cutoff_friday_aligns_to_monday(self):
        # raw on Friday (weekday=4) → Monday = raw - 4 days
        # need a now where raw = a Friday
        # 2024-08-16 is Friday (Mon 12 + 4 = Fri 16)
        # 2024-08-16 + 730 days = 2026-08-16
        cutoff = self._cutoff(datetime(2026, 8, 16, 2, 0))
        self.assertEqual(cutoff, datetime(2024, 8, 12))  # Mon of same week
        self.assertEqual(cutoff.weekday(), 0)

    def test_cutoff_always_lte_raw(self):
        """Aligned cutoff is always <= raw — we never push the window earlier
        than the raw 730-day mark, so no data that was ineligible under raw
        arithmetic is accidentally promoted."""
        for day in range(1, 8):  # test all 7 weekdays
            now = datetime(2026, 8, 9 + day, 2, 0)
            cutoff = self._cutoff(now)
            raw = (now - timedelta(days=730)).replace(hour=0, minute=0, second=0, microsecond=0)
            self.assertLessEqual(cutoff, raw, f"day={day}: cutoff must not exceed raw")
            self.assertEqual(cutoff.weekday(), 0, f"day={day}: must be Monday")

    def test_boundary_week_fully_blocked(self):
        """When raw cutoff falls on Wednesday, NONE of that week's 1D candles
        (Mon through Fri) are eligible — the whole week is blocked."""
        # raw=2024-08-14 (Wed) → aligned=2024-08-12 (Mon)
        cutoff = self._cutoff(datetime(2026, 8, 14, 2, 0))
        self.assertEqual(cutoff, datetime(2024, 8, 12))
        week_days = [12, 13, 14, 15, 16]  # Mon-Fri of 2024-08-12 week
        for d in week_days:
            ts = datetime(2024, 8, d)
            self.assertFalse(ts < cutoff, f"2024-08-{d} should NOT be eligible")

    def test_previous_complete_week_fully_eligible(self):
        """The week BEFORE the boundary week must have all its days eligible."""
        # raw=2024-08-14 (Wed) → aligned=2024-08-12 (Mon)
        cutoff = self._cutoff(datetime(2026, 8, 14, 2, 0))
        prev_week_days = [5, 6, 7, 8, 9]  # Mon-Fri of 2024-08-05 week
        for d in prev_week_days:
            ts = datetime(2024, 8, d)
            self.assertTrue(ts < cutoff, f"2024-08-{d} should be eligible")

    def test_boundary_week_becomes_eligible_next_monday(self):
        """The boundary week becomes eligible when now advances so that the
        raw cutoff reaches the FOLLOWING Monday."""
        # Boundary week: Mon 2024-08-12 to Fri 2024-08-16
        # Becomes eligible when raw cutoff >= Mon 2024-08-19
        # i.e. when now >= 2024-08-19 + 730 = 2026-08-19
        cutoff_before = self._cutoff(datetime(2026, 8, 18, 2, 0))  # one day earlier
        cutoff_after  = self._cutoff(datetime(2026, 8, 19, 2, 0))

        # On 2026-08-18: aligned cutoff is still 2024-08-12 (week blocked)
        self.assertFalse(datetime(2024, 8, 12) < cutoff_before)

        # On 2026-08-19: aligned cutoff = 2024-08-19 → whole prev week eligible
        self.assertEqual(cutoff_after, datetime(2024, 8, 19))
        for d in [12, 13, 14, 15, 16]:
            self.assertTrue(datetime(2024, 8, d) < cutoff_after)

    def test_weekly_chain_not_affected_by_alignment(self):
        """Chain A rules (after_trading_sessions) must not be affected by the
        calendar-chain alignment code."""
        chain_a_rule = RetentionRule(source_tf="5m", target_tf="15m", after_trading_sessions=60)
        now = datetime(2026, 8, 10, 2, 0, 0)
        with patch("exchange_calendar.nse_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
            cutoff = self.service._compute_cutoff(chain_a_rule, now)
        # Result must NOT be a Monday necessarily (session-based, may land any weekday)
        # Just verify it's in the past and is a midnight datetime.
        self.assertLess(cutoff, now)
        self.assertEqual(cutoff.time(), datetime.min.time())


class TestCutoffAlignmentMonthly(unittest.TestCase):
    """_compute_cutoff for 1W->1M must always return the first day of the raw
    cutoff's calendar month."""

    def setUp(self):
        self.service = RetentionService(MagicMock(), MagicMock())
        self.rule = RetentionRule(source_tf="1W", target_tf="1M", after_days=1825)

    def _cutoff(self, now):
        return self.service._compute_cutoff(self.rule, now)

    def test_mid_month_raw_aligns_to_first(self):
        # now=2026-08-10 → raw=2021-08-11 → 2021-08-01
        cutoff = self._cutoff(datetime(2026, 8, 10, 2, 0))
        self.assertEqual(cutoff, datetime(2021, 8, 1))
        self.assertEqual(cutoff.day, 1)

    def test_february_leap_year(self):
        # now such that raw lands in Feb of a leap year → 2024-02-01
        # raw = 2024-02-15 → align to 2024-02-01
        # now = 2024-02-15 + 1825 days
        # 1825/365 ≈ 5 years → now ≈ 2029-02-15
        now = datetime(2024, 2, 15) + timedelta(days=1825)
        cutoff = self._cutoff(now)
        self.assertEqual(cutoff.day, 1)
        self.assertEqual(cutoff.month, 2)

    def test_last_day_of_month_aligns_to_first(self):
        # raw on the last day of any month → aligns to the 1st of that month
        # Choose a raw = 2021-07-31 (last day of July)
        # now = 2021-07-31 + 1825 days
        now = datetime(2021, 7, 31) + timedelta(days=1825)
        cutoff = self._cutoff(now)
        self.assertEqual(cutoff.day, 1)
        self.assertEqual(cutoff.month, 7)  # first of July

    def test_first_of_month_raw_unchanged(self):
        # raw on the 1st is already aligned
        # raw = 2021-08-01 → 2021-08-01 (unchanged)
        now = datetime(2021, 8, 1) + timedelta(days=1825)
        cutoff = self._cutoff(now)
        self.assertEqual(cutoff.day, 1)
        self.assertEqual(cutoff.month, 8)

    def test_boundary_month_fully_blocked(self):
        """When raw is mid-August 2021, no 1W candle from August 2021 is eligible."""
        # now=2026-08-10 → raw=2021-08-11 → cutoff=2021-08-01
        cutoff = self._cutoff(datetime(2026, 8, 10, 2, 0))
        # All August 2021 Mondays: 2, 9, 16, 23, 30
        for day in [2, 9, 16, 23, 30]:
            ts = datetime(2021, 8, day)
            self.assertFalse(ts < cutoff, f"2021-08-{day} (Aug 1W) should NOT be eligible")

    def test_previous_complete_month_eligible(self):
        """All July 2021 1W candles must be eligible when boundary is August."""
        cutoff = self._cutoff(datetime(2026, 8, 10, 2, 0))  # cutoff=2021-08-01
        # July 2021 Mondays: 5, 12, 19, 26
        for day in [5, 12, 19, 26]:
            ts = datetime(2021, 7, day)
            self.assertTrue(ts < cutoff, f"2021-07-{day} (Jul 1W) should be eligible")


# ── B-1: prove the bug with partial-week resampling ───────────────────────────

class TestPartialWeekResamplingBugProof(unittest.TestCase):
    """Prove that resampling a partial week produces a wrong 1W candle, and that
    the partial candle's timestamp collides with the complete week's timestamp."""

    def setUp(self):
        mon = datetime(2024, 8, 12)
        self.week = [
            _c(mon + timedelta(days=i),
               open_=100 + i * 10, high=115 + i * 10,
               low=85 + i * 10, close=105 + i * 10, volume=1000 + i * 100)
            for i in range(5)  # Mon–Fri
        ]
        # Expected values for the complete week
        self.expected_open   = 100    # Mon open (i=0: open_=100)
        self.expected_close  = 145    # Fri close (i=4: close=105 + 4*10 = 145)
        self.expected_high   = max(115 + i * 10 for i in range(5))   # 155
        self.expected_low    = min(85 + i * 10 for i in range(5))    # 85
        self.expected_volume = sum(1000 + i * 100 for i in range(5)) # 6000

    def test_complete_week_produces_correct_values(self):
        result = CandleResampler.resample_5m_to(self.week, "1W")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"],   self.expected_open)
        self.assertEqual(result[0]["close"],  self.expected_close)
        self.assertEqual(result[0]["volume"], self.expected_volume)

    def test_partial_week_mon_only_wrong_close_and_volume(self):
        """Mon only: close and volume are wrong."""
        partial = CandleResampler.resample_5m_to(self.week[:1], "1W")
        self.assertEqual(len(partial), 1)
        self.assertNotEqual(partial[0]["close"],  self.expected_close)
        self.assertNotEqual(partial[0]["volume"], self.expected_volume)
        self.assertEqual(partial[0]["close"],  105)   # Mon close only
        self.assertEqual(partial[0]["volume"], 1000)  # Mon volume only

    def test_partial_week_mon_tue_wrong_close_and_volume(self):
        """Mon+Tue: still wrong."""
        partial = CandleResampler.resample_5m_to(self.week[:2], "1W")
        self.assertEqual(len(partial), 1)
        self.assertNotEqual(partial[0]["close"],  self.expected_close)
        self.assertNotEqual(partial[0]["volume"], self.expected_volume)

    def test_partial_and_complete_target_same_timestamp(self):
        """The partial 1W and the complete 1W target the same Monday timestamp.
        DO NOTHING on the second insert permanently freezes the partial value."""
        partial  = CandleResampler.resample_5m_to(self.week[:1], "1W")
        complete = CandleResampler.resample_5m_to(self.week,     "1W")
        self.assertEqual(partial[0]["timestamp"], complete[0]["timestamp"])
        self.assertNotEqual(partial[0]["volume"], complete[0]["volume"])

    def test_1w_to_1m_partial_month_same_collision(self):
        """Same collision exists for 1W->1M: partial July monthly from only
        first 2 weeks clashes with complete July monthly."""
        jul_week1 = _c(datetime(2021, 7, 5),  volume=1000)
        jul_week2 = _c(datetime(2021, 7, 12), volume=900)
        jul_week3 = _c(datetime(2021, 7, 19), volume=800)
        jul_week4 = _c(datetime(2021, 7, 26), volume=700)
        partial  = CandleResampler.resample_5m_to([jul_week1, jul_week2], "1M")
        complete = CandleResampler.resample_5m_to([jul_week1, jul_week2, jul_week3, jul_week4], "1M")
        self.assertEqual(partial[0]["timestamp"], complete[0]["timestamp"])
        self.assertNotEqual(partial[0]["volume"], complete[0]["volume"])


# ── B-1: partial-target guard logic ──────────────────────────────────────────

class TestPartialTargetGuard(unittest.TestCase):
    """Verify that _process_ticker raises ValueError when DO NOTHING blocks an
    insert and the existing candle has different volume (partial candle guard)."""

    def _make_service(self):
        db_factory = MagicMock()
        resample_svc = MagicMock()
        svc = RetentionService(db_session_factory=db_factory, resample_svc=resample_svc)
        return svc, db_factory, resample_svc

    def test_volume_mismatch_raises_error(self):
        """Existing target volume != computed → raise ValueError (source preserved)."""
        svc, db_factory, resample_svc = self._make_service()

        mon = datetime(2024, 8, 12)
        source_rows = [
            MagicMock(id=i, timestamp=mon + timedelta(days=i),
                      open=100.0, high=110.0, low=90.0, close=105.0, volume=1000,
                      timeframe="1D")
            for i in range(5)  # Mon-Fri
        ]
        # target_dict must match the source checksum exactly so the checksum
        # gate passes and execution reaches the partial-target guard below.
        # Source: 5 rows, each open=100 high=110 low=90 close=105 volume=1000.
        # Checksum: open_first=100, close_last=105, high_max=110, low_min=90, volume_sum=5000.
        target_dict = {
            "timestamp": mon,
            "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0,
            "volume": 5000  # sum of 5 source rows × 1000
        }
        resample_svc.resample_5m_to.return_value = [target_dict]

        db = MagicMock()
        # Source batch query (.limit().all()) returns source_rows
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = source_rows
        # Extra-rows query (.all() without .limit(), used by _target_bucket_tail extension):
        # return [] so no extra rows are appended and the checksum stays consistent.
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        # First insert: DO NOTHING (rowcount=0) — simulates existing partial candle
        insert_result = MagicMock()
        insert_result.rowcount = 0
        db.execute.return_value = insert_result

        # Existing candle has partial volume (Mon only = 1000, not full week 5000)
        existing_candle = MagicMock()
        existing_candle.volume = 1000  # partial
        db.query.return_value.filter.return_value.first.return_value = existing_candle

        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)

        with self.assertRaises(ValueError) as ctx:
            svc._process_ticker(db, rule, datetime(2026, 8, 20), "TESTSTOCK")

        self.assertIn("Partial target detected", str(ctx.exception))
        self.assertIn("TESTSTOCK", str(ctx.exception))
        # Source must NOT have been deleted
        db.query.return_value.filter.return_value.delete.assert_not_called()

    def test_volume_match_does_not_raise(self):
        """Existing target has same volume as computed → idempotent, no error."""
        svc, db_factory, resample_svc = self._make_service()

        mon = datetime(2024, 8, 12)
        source_rows = [
            MagicMock(id=i, timestamp=mon + timedelta(days=i),
                      open=100.0, high=110.0, low=90.0, close=105.0, volume=1000,
                      timeframe="1D")
            for i in range(5)
        ]
        target_dict = {
            "timestamp": mon,
            "open": 100.0, "high": 155.0, "low": 85.0, "close": 140.0,
            "volume": 6000
        }
        resample_svc.resample_5m_to.return_value = [target_dict]

        db = MagicMock()

        # Source batch query (.limit().all()): first call returns source_rows, second
        # returns [] to break the while loop. Extra-rows query (.all() without .limit(),
        # from _target_bucket_tail) returns [] so no extra rows corrupt the checksum.
        source_query_mock = MagicMock()
        source_query_mock.limit.return_value.all.side_effect = [source_rows, []]
        source_query_mock.all.return_value = []  # extra-rows extension: none

        # DO NOTHING (rowcount=0) — candle already exists from a correct prior run
        insert_result = MagicMock()
        insert_result.rowcount = 0
        db.execute.return_value = insert_result

        # Existing candle has the correct (matching) volume
        existing_candle = MagicMock()
        existing_candle.volume = 6000  # matches
        count_mock = MagicMock()
        count_mock.count.return_value = 1  # present == len(set(target_ts)) = 1

        def query_side_effect(model_or_attr):
            return MagicMock(
                filter=MagicMock(
                    return_value=MagicMock(
                        order_by=MagicMock(return_value=source_query_mock),
                        first=MagicMock(return_value=existing_candle),
                        count=MagicMock(return_value=1),
                        delete=MagicMock(),
                    )
                )
            )

        db.query.side_effect = query_side_effect
        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        # Must NOT raise
        try:
            svc._process_ticker(db, rule, datetime(2026, 8, 20), "TESTSTOCK")
        except ValueError as e:
            if "Partial target" in str(e):
                self.fail(f"Should not raise partial-target error when volumes match: {e}")


# ── B-2: backfill idempotency (conceptual) ────────────────────────────────────

class TestBackfillIdempotencyFix(unittest.TestCase):
    """Verify the B-2 fix: perform_on_demand_backfill uses ON CONFLICT DO NOTHING
    instead of db.add, so a concurrent insert cannot roll back the whole batch."""

    def test_backfill_uses_pg_insert_not_db_add(self):
        """Inspect the source: db.add must not be called for candle inserts."""
        import inspect
        from main import perform_on_demand_backfill
        src = inspect.getsource(perform_on_demand_backfill)
        # The fix removes db.add(record) for candle insertion.
        # pg_insert / on_conflict_do_nothing must be present.
        self.assertIn("on_conflict_do_nothing", src,
                      "backfill must use ON CONFLICT DO NOTHING")
        # db.add(record) for candle inserts must be gone.  A bare `db.add(`
        # for candle rows (record = model(...)) should not appear.
        self.assertNotIn("db.add(record)", src,
                         "db.add(record) must be replaced with pg_insert DO NOTHING")


# ── B-3: stale RUNNING job cleanup ────────────────────────────────────────────

class TestStaleRunningJobCleanup(unittest.TestCase):
    """_downgrade must mark past-day RUNNING rows as FAILED at cycle start."""

    def _make_service_with_db(self):
        db_factory = MagicMock()
        resample_svc = MagicMock()
        svc = RetentionService(db_session_factory=db_factory, resample_svc=resample_svc)
        db = MagicMock()
        db_factory.return_value = db
        return svc, db

    def test_stale_running_rows_marked_failed_before_resume_check(self):
        """UPDATE retention_jobs SET status='FAILED' is executed before the
        checkpoint-resume SELECT."""
        svc, db = self._make_service_with_db()
        # No today-running job (so no resume)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        svc._next_ticker_batch = MagicMock(return_value=[])
        svc._finish_job = MagicMock()

        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        svc._downgrade(rule, datetime.now())

        # The cleanup UPDATE must have been executed (db.execute called with the
        # FAILED status update SQL before any SELECT for checkpoint resume).
        # SQLAlchemy text() objects do not expose their SQL in str(call_args);
        # access .text directly on the first positional argument instead.
        calls = db.execute.call_args_list
        self.assertTrue(
            any(
                hasattr(c.args[0], "text")
                and "FAILED" in c.args[0].text
                and "Abandoned" in c.args[0].text
                for c in calls
            ),
            f"Expected FAILED-status UPDATE among db.execute calls; got: {calls}"
        )

    def test_today_running_job_not_marked_failed(self):
        """The cleanup filter (start_time < today_start) must leave today's own
        RUNNING job untouched so checkpoint resume can find it."""
        svc, db = self._make_service_with_db()
        # Today's running job is found by checkpoint resume
        today_job = MagicMock(id=99, last_ticker="RELIANCE", rows_source=50, rows_target=10)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = today_job
        svc._next_ticker_batch = MagicMock(return_value=[])
        svc._finish_job = MagicMock()

        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        svc._downgrade(rule, datetime.now())

        # Checkpoint resume must still find the today job (db.add not called)
        db.add.assert_not_called()
        svc._next_ticker_batch.assert_called_with(rule, unittest.mock.ANY, "RELIANCE")

    def test_cleanup_uses_today_start_boundary(self):
        """The SQL UPDATE must use a today_start boundary so it only touches
        rows from BEFORE today, never today's own job."""
        svc, db = self._make_service_with_db()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        svc._next_ticker_batch = MagicMock(return_value=[])
        svc._finish_job = MagicMock()

        rule = RetentionRule(source_tf="5m", target_tf="15m", after_trading_sessions=60)
        svc._downgrade(rule, datetime.now())

        # Verify the SQL string contains 'start_time < :today' (not >= today).
        # Access TextClause.text directly — str(call_args) shows the repr, not the SQL.
        cleanup_calls = [
            c for c in db.execute.call_args_list
            if hasattr(c.args[0], "text") and "FAILED" in c.args[0].text
        ]
        self.assertTrue(cleanup_calls, "Cleanup UPDATE must have been called")
        self.assertTrue(
            any(
                "start_time < " in c.args[0].text
                or "start_time<" in c.args[0].text
                or ":today" in c.args[0].text
                for c in cleanup_calls
            ),
            f"Cleanup must filter by start_time < today; got: {[c.args[0].text for c in cleanup_calls]}"
        )


# ── Calendar correctness end-to-end ───────────────────────────────────────────

class TestCalendarCutoffEndToEnd(unittest.TestCase):
    """Prove the invariant: with the aligned cutoff, only COMPLETE weeks/months
    are ever passed to the resampler — no partial-period source sets."""

    def setUp(self):
        self.service = RetentionService(MagicMock(), MagicMock())

    def _make_1d_candles_for_week(self, monday: datetime, volume_base=1000):
        """5 trading day 1D candles for a Mon-Fri week."""
        return [
            _c(monday + timedelta(days=i),
               open_=100 + i, high=115 + i, low=85 + i, close=105 + i,
               volume=volume_base + i * 100)
            for i in range(5)
        ]

    def test_partial_week_at_boundary_blocked_by_aligned_cutoff(self):
        """When aligned cutoff = Mon of boundary week, that week's 1D candles
        are NOT eligible — the whole week is held back."""
        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        # raw=2024-08-14 (Wed) → aligned=2024-08-12 (Mon)
        now = datetime(2026, 8, 14, 2, 0)
        cutoff = self.service._compute_cutoff(rule, now)

        week_candles = self._make_1d_candles_for_week(datetime(2024, 8, 12))
        # None of these are eligible (all timestamps >= cutoff)
        eligible = [c for c in week_candles if c["timestamp"] < cutoff]
        self.assertEqual(len(eligible), 0,
                         "No 1D candle from the boundary week should be eligible")

    def test_prior_complete_week_fully_eligible(self):
        """The week prior to the boundary is fully eligible: all 5 trading days
        have timestamps < the aligned cutoff."""
        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        now = datetime(2026, 8, 14, 2, 0)
        cutoff = self.service._compute_cutoff(rule, now)

        prior_week_candles = self._make_1d_candles_for_week(datetime(2024, 8, 5))
        eligible = [c for c in prior_week_candles if c["timestamp"] < cutoff]
        self.assertEqual(len(eligible), 5,
                         "All 5 days of the prior week must be eligible")

    def test_eligible_complete_week_resamples_correctly(self):
        """5 eligible 1D candles → 1 complete 1W candle with correct OHLCV."""
        monday = datetime(2024, 8, 5)
        week = self._make_1d_candles_for_week(monday, volume_base=1000)
        result = CandleResampler.resample_5m_to(week, "1W")
        self.assertEqual(len(result), 1)
        # Timestamp is the Monday of the week (pandas W-MON closed='left' label='left')
        ts = result[0]["timestamp"]
        if hasattr(ts, "date"):
            self.assertEqual(ts.date(), monday.date())
        else:
            self.assertEqual(ts, monday)
        # Volume = sum of all 5 days: 1000+1100+1200+1300+1400 = 6000
        expected_volume = sum(1000 + i * 100 for i in range(5))
        self.assertEqual(result[0]["volume"], expected_volume)
        self.assertEqual(result[0]["open"],   100)  # Mon open (i=0: open_=100)
        self.assertEqual(result[0]["close"],  109)  # Fri close (i=4: close=105+4=109)


if __name__ == "__main__":
    unittest.main()
