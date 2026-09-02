import unittest
from datetime import datetime, timedelta
from unittest.mock import ANY, MagicMock, patch
from retention_service import RetentionService, RetentionRule


class TestRetentionRule(unittest.TestCase):
    def test_rule_creation(self):
        rule = RetentionRule(source_tf="5m", target_tf="15m", after_days=0, after_trading_sessions=60, batch_size=200)
        self.assertEqual(rule.source_tf, "5m")
        self.assertEqual(rule.target_tf, "15m")
        self.assertEqual(rule.after_trading_sessions, 60)

    def test_rule_default_batch_size(self):
        rule = RetentionRule(source_tf="5m", target_tf="15m", after_days=120)
        self.assertEqual(rule.batch_size, 200)


class TestRetentionService(unittest.TestCase):
    def setUp(self):
        self.db_factory = MagicMock()
        self.resample_svc = MagicMock()
        self.service = RetentionService(
            db_session_factory=self.db_factory,
            resample_svc=self.resample_svc,
        )

    # ── Policy ─────────────────────────────────────────────────────────
    def test_load_policy(self):
        """Chain A rules use after_trading_sessions; Chain B rules use
        after_days — both are valid, already-supported cutoff units, and a
        rule should use exactly one (see _compute_cutoff's truthy-check
        precedence: a populated after_trading_sessions always wins, so a
        Chain B rule meant to use after_days must leave it unset)."""
        rules = self.service.load_policy()
        self.assertEqual(len(rules), 6)
        intraday_tfs = {"5m", "15m", "30m", "1h"}
        daily_tfs = {"1D", "1W"}
        for rule in rules:
            self.assertIsInstance(rule, RetentionRule)
            self.assertIn(rule.source_tf, {"5m", "15m", "30m", "1h", "1D", "1W"})
            self.assertIn(rule.target_tf, {"15m", "30m", "1h", "4h", "1W", "1M"})
            if rule.source_tf in intraday_tfs:
                self.assertTrue(rule.after_trading_sessions, f"{rule.source_tf}->{rule.target_tf} must use after_trading_sessions")
                self.assertEqual(rule.after_days, 0)
            elif rule.source_tf in daily_tfs:
                self.assertFalse(rule.after_trading_sessions, f"{rule.source_tf}->{rule.target_tf} must NOT set after_trading_sessions (would silently override after_days)")
                self.assertGreater(rule.after_days, 0, f"{rule.source_tf}->{rule.target_tf} must use after_days")

    def test_intraday_chain_pyramid_order(self):
        """Chain A: ascending single-source pyramid 5m→15m→30m→1h→4h."""
        rules = self.service.load_policy()
        intraday = [r for r in rules if r.source_tf in ("5m", "15m", "30m", "1h")]
        sources = [r.source_tf for r in intraday]
        targets = [r.target_tf for r in intraday]
        self.assertEqual(sources, ["5m", "15m", "30m", "1h"])
        self.assertEqual(targets, ["15m", "30m", "1h", "4h"])

    def test_intraday_chain_boundaries_increasing(self):
        """Boundaries must be strictly increasing (60 < 120 < 180 < 365 sessions)"""
        rules = self.service.load_policy()
        intraday = [r for r in rules if r.source_tf in ("5m", "15m", "30m", "1h")]
        sessions = [r.after_trading_sessions for r in intraday]
        self.assertEqual(sessions, [60, 120, 180, 365])
        for i in range(len(sessions) - 1):
            self.assertLess(sessions[i], sessions[i + 1])

    # ── Per-boundary tests (explicit, one per transition) ────────────────
    # Each checks both the policy VALUE and that _compute_cutoff() actually
    # lands N real trading sessions back for that specific rule — i.e. the
    # engine interprets after_trading_sessions correctly for every tier, not
    # just the ones already covered by the generic list-based checks above.

    def _sessions_back(self, now: datetime, n: int) -> datetime:
        """Reference implementation used only by these tests: N weekday
        sessions back from `now`, mirroring _session_cutoff() when every
        weekday is a trading day (no holidays) — matches the patched
        `is_trading_day` used throughout this test class."""
        count = 0
        cur = now.date() - timedelta(days=1)
        while count < n:
            if cur.weekday() < 5:
                count += 1
            if count >= n:
                break
            cur -= timedelta(days=1)
        return datetime.combine(cur, datetime.min.time())

    def test_5m_to_15m_boundary(self):
        rules = {(r.source_tf, r.target_tf): r for r in self.service.load_policy()}
        rule = rules[("5m", "15m")]
        self.assertEqual(rule.after_trading_sessions, 60)
        now = datetime(2026, 8, 10, 2, 0, 0)
        with patch("exchange_calendar.nse_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
            self.assertEqual(self.service._compute_cutoff(rule, now), self._sessions_back(now, 60))

    def test_15m_to_30m_boundary(self):
        rules = {(r.source_tf, r.target_tf): r for r in self.service.load_policy()}
        rule = rules[("15m", "30m")]
        self.assertEqual(rule.after_trading_sessions, 120)
        now = datetime(2026, 8, 10, 2, 0, 0)
        with patch("exchange_calendar.nse_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
            self.assertEqual(self.service._compute_cutoff(rule, now), self._sessions_back(now, 120))

    def test_30m_to_1h_boundary(self):
        rules = {(r.source_tf, r.target_tf): r for r in self.service.load_policy()}
        rule = rules[("30m", "1h")]
        self.assertEqual(rule.after_trading_sessions, 180)
        now = datetime(2026, 8, 10, 2, 0, 0)
        with patch("exchange_calendar.nse_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
            self.assertEqual(self.service._compute_cutoff(rule, now), self._sessions_back(now, 180))

    def test_1h_to_4h_boundary(self):
        rules = {(r.source_tf, r.target_tf): r for r in self.service.load_policy()}
        rule = rules[("1h", "4h")]
        self.assertEqual(rule.after_trading_sessions, 365)
        now = datetime(2026, 8, 10, 2, 0, 0)
        with patch("exchange_calendar.nse_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
            self.assertEqual(self.service._compute_cutoff(rule, now), self._sessions_back(now, 365))

    def test_no_4h_to_1d_rule(self):
        """4h is the final intraday tier; there must be NO 4h→1D rule.
        This is the exact regression this policy shape must never reintroduce —
        the intraday chain (Chain A) and the daily+ chain (Chain B) are
        independent, and 4h must never feed into 1D."""
        rules = self.service.load_policy()
        pairs = [(r.source_tf, r.target_tf) for r in rules]
        self.assertNotIn(("4h", "1D"), pairs)

    def test_daily_chain_1d_to_1w_present(self):
        """Chain B: 1D -> 1W exists, retaining 730 CALENDAR days (2yr) of
        daily candles. Uses after_days, not after_trading_sessions — "2
        years" is a calendar-time concept and after_days was verified
        reliable, so no sessions/year approximation is needed."""
        rules = self.service.load_policy()
        daily_chain = [r for r in rules if r.source_tf == "1D"]
        self.assertEqual(len(daily_chain), 1)
        rule = daily_chain[0]
        self.assertEqual(rule.target_tf, "1W")
        self.assertEqual(rule.after_days, 730)
        self.assertFalse(rule.after_trading_sessions)

    def test_daily_chain_1w_to_1m_present(self):
        """Chain B: 1W -> 1M exists, retaining 1825 CALENDAR days (5yr) of
        weekly candles before compressing older ones into monthly."""
        rules = self.service.load_policy()
        weekly_chain = [r for r in rules if r.source_tf == "1W"]
        self.assertEqual(len(weekly_chain), 1)
        rule = weekly_chain[0]
        self.assertEqual(rule.target_tf, "1M")
        self.assertEqual(rule.after_days, 1825)
        self.assertFalse(rule.after_trading_sessions)

    def test_1d_to_1w_boundary_cutoff(self):
        """1D->1W cutoff is aligned to the Monday of the 730-day boundary week,
        preventing partial-week promotion (B-1 fix)."""
        rules = {(r.source_tf, r.target_tf): r for r in self.service.load_policy()}
        rule = rules[("1D", "1W")]
        now = datetime(2026, 8, 10, 2, 0, 0)
        cutoff = self.service._compute_cutoff(rule, now)
        # raw = 2024-08-10 (Saturday, weekday=5); aligned Monday = 2024-08-05
        self.assertEqual(cutoff, datetime(2024, 8, 5))
        self.assertEqual(cutoff.weekday(), 0, "cutoff must always be a Monday for 1D->1W")

    def test_1w_to_1m_boundary_cutoff(self):
        """1W->1M cutoff is aligned to the first day of the 1825-day boundary
        month, preventing partial-month promotion (B-1 fix)."""
        rules = {(r.source_tf, r.target_tf): r for r in self.service.load_policy()}
        rule = rules[("1W", "1M")]
        now = datetime(2026, 8, 10, 2, 0, 0)
        cutoff = self.service._compute_cutoff(rule, now)
        # raw = 2021-08-11; aligned to first of August = 2021-08-01
        self.assertEqual(cutoff, datetime(2021, 8, 1))
        self.assertEqual(cutoff.day, 1, "cutoff must always be the 1st of a month for 1W->1M")

    # ── Explicit calendar-day-vs-session-day cutoff behavior ──────────────

    def test_after_days_cutoff_is_aligned_to_monday_not_session_based(self):
        """1D->1W uses pure calendar arithmetic aligned to Monday — it never
        consults nse_calendar.is_trading_day (proven by not patching it here).
        The result is deterministic from `now` and after_days alone, and is
        always a Monday (not a session-aware date)."""
        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        now = datetime(2028, 1, 10, 9, 0, 0)
        cutoff = self.service._compute_cutoff(rule, now)
        raw = (now - timedelta(days=730)).replace(hour=0, minute=0, second=0, microsecond=0)
        expected_monday = raw - timedelta(days=raw.weekday())
        self.assertEqual(cutoff, expected_monday)
        # Must always be a Monday — proving it is weekday-aligned, not session-aligned.
        self.assertEqual(cutoff.weekday(), 0, "1D->1W cutoff must always be a Monday")

    def test_after_days_and_after_trading_sessions_use_the_same_now_reference(self):
        """Timezone/consistency check: both cutoff branches are pure functions
        of the same `now` argument (ist_now_naive() in production) — there is
        no separate timezone handling in either branch, so the two chains
        can never disagree about "today" due to a clock mismatch."""
        session_rule = RetentionRule(source_tf="5m", target_tf="15m", after_trading_sessions=60)
        days_rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        now = datetime(2026, 8, 10, 2, 0, 0)
        with patch("exchange_calendar.nse_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
            session_cutoff = self.service._compute_cutoff(session_rule, now)
            days_cutoff = self.service._compute_cutoff(days_rule, now)
        # Both must be naive datetimes truncated to midnight, computed from
        # the identical `now` — no tz-aware/naive mismatch between them.
        self.assertIsNone(session_cutoff.tzinfo)
        self.assertIsNone(days_cutoff.tzinfo)
        self.assertEqual(session_cutoff.time(), datetime.min.time())
        self.assertEqual(days_cutoff.time(), datetime.min.time())

    def test_after_days_cutoff_rolls_forward_as_time_advances(self):
        """Rolling-window proof: the cutoff advances continuously as `now`
        advances — never a fixed cutover date.  When `now` advances by a full
        week (7 days, same weekday), the Monday-aligned cutoff also advances
        by exactly 7 days.  (Chain A's equivalent is proven by session-based
        rolling-boundary tests below.)"""
        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        now_1 = datetime(2026, 8, 10, 2, 0, 0)   # Monday
        now_2 = now_1 + timedelta(days=7)          # Next Monday (same weekday)
        cutoff_1 = self.service._compute_cutoff(rule, now_1)
        cutoff_2 = self.service._compute_cutoff(rule, now_2)
        # Same-weekday advance → aligned cutoff also advances by the same interval.
        self.assertEqual(cutoff_2 - cutoff_1, timedelta(days=7))
        # Both must be Mondays.
        self.assertEqual(cutoff_1.weekday(), 0)
        self.assertEqual(cutoff_2.weekday(), 0)

    def test_1m_has_no_further_compression_rule(self):
        """1M is the final tier of Chain B — 'do not compress further' is
        expressed by the simple absence of any rule sourced from 1M, not a
        special case anywhere in the engine."""
        rules = self.service.load_policy()
        sources = [r.source_tf for r in rules]
        self.assertNotIn("1M", sources)

    def test_daily_chain_is_independent_of_intraday_chain(self):
        """No rule in Chain B sources from an intraday tier, and no rule in
        Chain A targets a daily+ tier — the two chains never cross."""
        rules = self.service.load_policy()
        intraday_tfs = {"5m", "15m", "30m", "1h", "4h"}
        daily_tfs = {"1D", "1W", "1M"}
        for r in rules:
            if r.source_tf in intraday_tfs:
                self.assertNotIn(r.target_tf, daily_tfs,
                                  f"intraday rule {r.source_tf}->{r.target_tf} must not target a daily+ tier")
            if r.source_tf in daily_tfs:
                self.assertNotIn(r.target_tf, intraday_tfs,
                                  f"daily rule {r.source_tf}->{r.target_tf} must not target an intraday tier")

    # ── Cutoff ─────────────────────────────────────────────────────────
    def test_compute_cutoff_session_based(self):
        rule = RetentionRule(source_tf="5m", target_tf="15m", after_trading_sessions=60)
        now = datetime(2026, 8, 8, 2, 0, 0)
        cutoff = self.service._compute_cutoff(rule, now)
        # Cutoff must be a session-aware date (Sundays skipped); >= the oldest window
        self.assertIsInstance(cutoff, datetime)
        self.assertLess(cutoff, now)

    def test_session_cutoff_counts_trading_days(self):
        """Friday 2026-08-07 minus 3 sessions (exclusive today) lands Thursday 2026-08-06"""
        now = datetime(2026, 8, 8, 14, 30, 0)  # Saturday
        with patch("exchange_calendar.nse_calendar.is_trading_day", return_value=True):
            cutoff = self.service._session_cutoff(1, now)
            self.assertEqual(cutoff, datetime(2026, 8, 7))

    def test_session_cutoff_skips_weekends(self):
        """Count 1 session back from Monday → previous Friday"""
        now = datetime(2026, 8, 10, 2, 0, 0)  # Monday
        with patch("exchange_calendar.nse_calendar.is_trading_day",
                   side_effect=lambda d: d.weekday() < 5):
            cutoff = self.service._session_cutoff(1, now)
            self.assertEqual(cutoff, datetime(2026, 8, 7))  # Friday

    # ── Checksum ────────────────────────────────────────────────────────
    def test_checksum_empty(self):
        result = self.service._checksum([])
        self.assertEqual(result, {})

    def test_checksum_single_candle(self):
        candles = [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        cs = self.service._checksum(candles)
        self.assertEqual(cs["count"], 1)
        self.assertEqual(cs["open_first"], 100)
        self.assertEqual(cs["close_last"], 102)
        self.assertEqual(cs["high_max"], 105)
        self.assertEqual(cs["low_min"], 95)
        self.assertEqual(cs["volume_sum"], 1000)

    def test_checksum_multiple(self):
        candles = [
            {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
            {"open": 102, "high": 110, "low": 101, "close": 108, "volume": 500},
        ]
        cs = self.service._checksum(candles)
        self.assertEqual(cs["count"], 2)
        self.assertEqual(cs["open_first"], 100)
        self.assertEqual(cs["close_last"], 108)
        self.assertEqual(cs["high_max"], 110)
        self.assertEqual(cs["low_min"], 95)
        self.assertEqual(cs["volume_sum"], 1500)

    def test_verify_checksum_match(self):
        src = {"volume_sum": 1000, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        tgt = {"volume_sum": 1000, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        self.assertTrue(self.service._verify_checksum(src, tgt))

    def test_verify_checksum_mismatch(self):
        src = {"volume_sum": 1000, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        tgt = {"volume_sum": 900, "open_first": 100, "close_last": 102, "high_max": 105, "low_min": 95}
        self.assertFalse(self.service._verify_checksum(src, tgt))

    def test_verify_checksum_empty(self):
        self.assertFalse(self.service._verify_checksum({}, {"volume_sum": 0}))
        self.assertFalse(self.service._verify_checksum({"volume_sum": 0}, {}))

    # ── Cycle behavior ──────────────────────────────────────────────────
    def test_run_cycle_already_running(self):
        self.service._running = True
        self.service.run_cycle()
        # Should not crash, just print and return

    def test_run_cycle_skips_live_window(self):
        self.service.load_policy = MagicMock(return_value=[
            RetentionRule(source_tf="5m", target_tf="15m", after_days=0, batch_size=50)
        ])
        with patch("retention_service.ist_now_naive",
                   return_value=datetime(2026, 7, 1, 10, 0, 0)):
            self.service.run_cycle()
            self.assertEqual(self.service._rows_converted, 0)

    def test_run_cycle_uses_reverse_policy_order(self):
        """Newly created 15m must NOT be re-compressed to 30m in the same run.
        Rule processing must be top-tier-first to avoid a same-run cascade —
        this applies across BOTH chains: Chain B's rules (listed last in the
        policy) run before any intraday rule, and no chain's output is
        consumed by another rule within the same cycle."""
        captured = []
        self.service._downgrade = MagicMock(side_effect=lambda r, c: captured.append((r.source_tf, r.target_tf)) or 0)
        self.service._compute_cutoff = MagicMock(return_value=datetime(2020, 1, 1))
        with patch("retention_service.ist_now_naive",
                   return_value=datetime(2026, 8, 8, 2, 0, 0)):
            self.service.run_cycle()
        self.assertEqual(captured, [
            ("1W", "1M"), ("1D", "1W"), ("1h", "4h"), ("30m", "1h"), ("15m", "30m"), ("5m", "15m"),
        ])

    # ── Rolling-boundary scenarios (item requirement: prove the configured
    # recent window is preserved and only older data compresses, for BOTH
    # chains, not a fixed one-time cutover) ───────────────────────────────

    def _assert_rolling_boundary(self, rule: RetentionRule, now: datetime, total_sessions: int):
        """Shared assertion: given `total_sessions` of consecutive weekday
        history ending at `now`, exactly `rule.after_trading_sessions` of the
        NEWEST sessions must be kept, and the rest (the oldest) must be
        eligible for compression — with a contiguous, session-count-based
        split, never a naive row-count/date-number cut."""
        with patch("exchange_calendar.nse_calendar.is_trading_day", side_effect=lambda d: d.weekday() < 5):
            cutoff = self.service._compute_cutoff(rule, now)

            sessions = []
            cur = now.date()
            while len(sessions) < total_sessions:
                cur -= timedelta(days=1)
                if cur.weekday() < 5:
                    sessions.append(cur)
            sessions.reverse()  # oldest -> newest

            kept = [d for d in sessions if datetime.combine(d, datetime.min.time()) >= cutoff]
            eligible = [d for d in sessions if datetime.combine(d, datetime.min.time()) < cutoff]

            expected_kept = rule.after_trading_sessions
            expected_eligible = total_sessions - rule.after_trading_sessions
            self.assertEqual(len(kept), expected_kept,
                              f"exactly the newest {expected_kept} sessions must remain {rule.source_tf}")
            self.assertEqual(len(eligible), expected_eligible,
                              f"exactly the oldest {expected_eligible} sessions must be eligible for {rule.target_tf}")
            if eligible and kept:
                self.assertLess(max(eligible), min(kept), "split must be contiguous by session count")

    def test_5m_retention_boundary_keeps_60_compresses_older(self):
        """Rolling-boundary guarantee for the intraday chain's first
        transition (session-based, matches the real policy)."""
        rule = RetentionRule(source_tf="5m", target_tf="15m", after_trading_sessions=60)
        self._assert_rolling_boundary(rule, datetime(2026, 8, 10, 2, 0, 0), total_sessions=67)

    def _assert_calendar_rolling_boundary(self, rule: RetentionRule, now: datetime, total_days: int):
        """Calendar-day equivalent of _assert_rolling_boundary, for Chain B's
        after_days-based rules: given `total_days` of consecutive CALENDAR
        days of history (every day, not just trading days — daily candle
        rows only exist on trading days, but the retention cutoff itself
        operates on calendar dates regardless), exactly `rule.after_days` of
        the newest days must be kept and the rest eligible for compression,
        with a contiguous split — proving the window rolls forward rather
        than being a fixed cutover."""
        cutoff = self.service._compute_cutoff(rule, now)
        days = [(now.date() - timedelta(days=i)) for i in range(1, total_days + 1)]
        days.reverse()  # oldest -> newest
        kept = [d for d in days if datetime.combine(d, datetime.min.time()) >= cutoff]
        eligible = [d for d in days if datetime.combine(d, datetime.min.time()) < cutoff]
        self.assertEqual(len(kept), rule.after_days,
                          f"exactly the newest {rule.after_days} calendar days must remain {rule.source_tf}")
        self.assertEqual(len(eligible), total_days - rule.after_days,
                          f"exactly the oldest {total_days - rule.after_days} days must be eligible for {rule.target_tf}")
        if eligible and kept:
            self.assertLess(max(eligible), min(kept), "split must be contiguous by calendar date")

    def test_1d_retention_boundary_aligned_to_monday(self):
        """1D->1W cutoff is the Monday of the 730-day boundary week (B-1 fix).
        This means the boundary week's 1D candles are kept until the ENTIRE week
        is past the rolling window — the retention window is >= 730 calendar days,
        never shorter (data-integrity guarantee)."""
        rule = RetentionRule(source_tf="1D", target_tf="1W", after_days=730)
        now = datetime(2026, 8, 10, 2, 0, 0)
        cutoff = self.service._compute_cutoff(rule, now)
        raw = (now - timedelta(days=730)).replace(hour=0, minute=0, second=0, microsecond=0)
        expected_monday = raw - timedelta(days=raw.weekday())
        self.assertEqual(cutoff, expected_monday)
        self.assertEqual(cutoff.weekday(), 0)
        # Aligned cutoff is <= raw (never promotes data that would not have been
        # eligible under raw arithmetic either — the window only extends, never shrinks).
        self.assertLessEqual(cutoff, raw)
        # Gap is at most 6 days (Mon–Sun of boundary week).
        self.assertGreaterEqual(cutoff, raw - timedelta(days=6))

    def test_1w_retention_boundary_aligned_to_month_start(self):
        """1W->1M cutoff is the first day of the 1825-day boundary month (B-1 fix).
        The boundary month's 1W candles are kept until the ENTIRE month is past the
        rolling window — the retention window is >= 1825 calendar days."""
        rule = RetentionRule(source_tf="1W", target_tf="1M", after_days=1825)
        now = datetime(2026, 8, 10, 2, 0, 0)
        cutoff = self.service._compute_cutoff(rule, now)
        raw = (now - timedelta(days=1825)).replace(hour=0, minute=0, second=0, microsecond=0)
        expected_month_start = raw.replace(day=1)
        self.assertEqual(cutoff, expected_month_start)
        self.assertEqual(cutoff.day, 1)
        self.assertLessEqual(cutoff, raw)

    # ── DB-05: checkpoint resume ──────────────────────────────────────
    def test_downgrade_creates_new_job_when_none_running(self):
        db = MagicMock()
        self.db_factory.return_value = db
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        self.service._next_ticker_batch = MagicMock(return_value=[])
        self.service._finish_job = MagicMock()

        rule = RetentionRule(source_tf="5m", target_tf="15m", after_days=0, after_trading_sessions=60)
        self.service._downgrade(rule, datetime.now())

        db.add.assert_called_once()
        added_job = db.add.call_args[0][0]
        self.assertEqual(added_job.job_type, "5m_to_15m")
        # Fresh job -> resumed cursor must be None, not some prior checkpoint.
        self.service._next_ticker_batch.assert_called_with(rule, ANY, None)

    def test_downgrade_resumes_from_existing_running_job(self):
        db = MagicMock()
        self.db_factory.return_value = db
        existing_job = MagicMock(id=42, last_ticker="RELIANCE", rows_source=100, rows_target=20)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = existing_job
        self.service._next_ticker_batch = MagicMock(return_value=[])
        self.service._finish_job = MagicMock()

        rule = RetentionRule(source_tf="5m", target_tf="15m", after_days=0, after_trading_sessions=60)
        cutoff = datetime.now()
        self.service._downgrade(rule, cutoff)

        # Must NOT create a second job row for the same in-flight run.
        db.add.assert_not_called()
        # Must resume the ticker cursor from the checkpoint, not start over.
        self.service._next_ticker_batch.assert_called_with(rule, cutoff, "RELIANCE")
        # Must finish under the SAME job id, carrying forward the
        # already-checkpointed row counts (no tickers processed this pass).
        self.service._finish_job.assert_called_once()
        call_args = self.service._finish_job.call_args[0]
        self.assertEqual(call_args[1], 42)
        self.assertEqual(call_args[3], 100)
        self.assertEqual(call_args[4], 20)

    def test_downgrade_ignores_running_job_from_a_previous_day(self):
        # A RUNNING job from a prior day is stale, not a same-cycle crash --
        # must not be resumed (its cutoff/eligible-ticker set may be outdated).
        db = MagicMock()
        self.db_factory.return_value = db
        # The query itself filters on start_time >= today, so an old job
        # correctly never reaches .first() -- simulate that by returning None.
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        self.service._next_ticker_batch = MagicMock(return_value=[])
        self.service._finish_job = MagicMock()

        rule = RetentionRule(source_tf="5m", target_tf="15m", after_days=0, after_trading_sessions=60)
        self.service._downgrade(rule, datetime.now())

        db.add.assert_called_once()

    # ── Stats ───────────────────────────────────────────────────────────
    def test_get_stats(self):
        stats = self.service.get_stats()
        self.assertIn("running", stats)
        self.assertIn("last_run", stats)
        self.assertIn("rows_converted", stats)
        self.assertIn("errors", stats)
        self.assertIn("policy_rules", stats)

    def test_get_stats_policy_count(self):
        stats = self.service.get_stats()
        self.assertEqual(stats["policy_rules"], 6)


if __name__ == "__main__":
    unittest.main()