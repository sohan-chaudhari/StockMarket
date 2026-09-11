import unittest
from datetime import date, timedelta

from migration.plan_1d import build_1d_migration_plan, TickerFetchPlan, MigrationPlan


class TestBuild1DMigrationPlan(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 8, 11)
        self.retention_days = 730
        self.required_start = self.today - timedelta(days=730)

    def test_complete_history_ticker_needs_no_fetch(self):
        rows = [{"ticker": "OLDSTOCK", "exchange": "NSE"}]
        existing = {"OLDSTOCK": self.required_start - timedelta(days=10)}
        plan = build_1d_migration_plan(rows, existing, self.today, self.retention_days, max_chunk_days=90)
        p = plan.ticker_plans[0]
        self.assertFalse(p.needs_fetch)
        self.assertEqual(p.chunks, [])
        self.assertIn(p, plan.complete_tickers)

    def test_partial_history_ticker_fetches_only_the_gap(self):
        rows = [{"ticker": "RECENT", "exchange": "NSE"}]
        existing_start = date(2026, 7, 21)
        existing = {"RECENT": existing_start}
        plan = build_1d_migration_plan(rows, existing, self.today, self.retention_days, max_chunk_days=90)
        p = plan.ticker_plans[0]
        self.assertTrue(p.needs_fetch)
        self.assertEqual(p.fetch_range, (self.required_start, existing_start - timedelta(days=1)))
        self.assertIn(p, plan.partial_history_tickers)
        self.assertNotIn(p, plan.no_history_tickers)

    def test_no_history_ticker_gets_full_required_range(self):
        rows = [{"ticker": "BRANDNEW", "exchange": "NSE"}]
        plan = build_1d_migration_plan(rows, {}, self.today, self.retention_days, max_chunk_days=90)
        p = plan.ticker_plans[0]
        self.assertTrue(p.needs_fetch)
        self.assertEqual(p.fetch_range, (self.required_start, self.today))
        self.assertIn(p, plan.no_history_tickers)
        self.assertIsNone(p.existing_start)

    def test_730_day_range_is_calendar_based_not_session_based(self):
        """The literal instruction: required_start = today - 730 calendar
        days, never converted to trading sessions."""
        rows = [{"ticker": "X", "exchange": "NSE"}]
        plan = build_1d_migration_plan(rows, {}, self.today, retention_days=730, max_chunk_days=90)
        self.assertEqual(plan.required_start, self.today - timedelta(days=730))
        self.assertEqual((self.today - plan.required_start).days, 730)

    def test_chunks_remain_contiguous_across_the_full_gap(self):
        rows = [{"ticker": "GAPPY", "exchange": "NSE"}]
        plan = build_1d_migration_plan(rows, {}, self.today, self.retention_days, max_chunk_days=90)
        p = plan.ticker_plans[0]
        self.assertEqual(p.chunks[0][0], p.fetch_range[0])
        self.assertEqual(p.chunks[-1][1], p.fetch_range[1])
        for i in range(len(p.chunks) - 1):
            self.assertEqual(p.chunks[i + 1][0], p.chunks[i][1] + timedelta(days=1))

    def test_dual_listed_ticker_produces_one_plan_not_two(self):
        rows = [
            {"ticker": "DUAL", "exchange": "NSE"},
            {"ticker": "DUAL", "exchange": "BSE"},
        ]
        plan = build_1d_migration_plan(rows, {}, self.today, self.retention_days, max_chunk_days=90)
        self.assertEqual(len(plan.ticker_plans), 1)
        self.assertEqual(plan.ticker_plans[0].identity.exchange, "NSE")
        self.assertEqual(len(plan.shadowed), 1)
        self.assertEqual(plan.shadowed[0].excluded_exchange, "BSE")

    def test_token_resolver_is_invoked_and_recorded_per_ticker(self):
        calls = []

        def resolver(ticker, exchange):
            calls.append((ticker, exchange))
            return ticker != "UNRESOLVABLE"

        rows = [
            {"ticker": "GOODTICKER", "exchange": "NSE"},
            {"ticker": "UNRESOLVABLE", "exchange": "NSE"},
        ]
        plan = build_1d_migration_plan(rows, {}, self.today, self.retention_days, max_chunk_days=90, token_resolver=resolver)
        self.assertEqual(set(calls), {("GOODTICKER", "NSE"), ("UNRESOLVABLE", "NSE")})
        self.assertEqual(len(plan.unresolvable_token_tickers), 1)
        self.assertEqual(plan.unresolvable_token_tickers[0].identity.ticker, "UNRESOLVABLE")

    def test_no_token_resolver_leaves_resolvability_unknown_not_false(self):
        """Absence of a resolver must not be misreported as 'unresolvable' --
        that would be a false negative in the dry-run report."""
        rows = [{"ticker": "X", "exchange": "NSE"}]
        plan = build_1d_migration_plan(rows, {}, self.today, self.retention_days, max_chunk_days=90)
        self.assertIsNone(plan.ticker_plans[0].token_resolvable)
        self.assertEqual(plan.unresolvable_token_tickers, [])

    def test_estimate_requests_matches_total_chunks(self):
        rows = [{"ticker": "A", "exchange": "NSE"}, {"ticker": "B", "exchange": "NSE"}]
        plan = build_1d_migration_plan(rows, {}, self.today, self.retention_days, max_chunk_days=90)
        self.assertEqual(plan.estimate_requests(), plan.total_chunks)
        self.assertGreater(plan.total_chunks, 0)

    def test_estimate_runtime_uses_rate_and_workers(self):
        rows = [{"ticker": "A", "exchange": "NSE"}]
        plan = build_1d_migration_plan(rows, {}, self.today, self.retention_days, max_chunk_days=90)
        fast = plan.estimate_runtime_seconds(requests_per_second=10, workers=10)
        slow = plan.estimate_runtime_seconds(requests_per_second=0.3, workers=1)
        self.assertLess(fast, slow)

    def test_mixed_universe_classifies_every_ticker_correctly(self):
        rows = [
            {"ticker": "COMPLETE", "exchange": "NSE"},
            {"ticker": "PARTIAL", "exchange": "NSE"},
            {"ticker": "MISSING", "exchange": "NSE"},
        ]
        existing = {
            "COMPLETE": self.required_start - timedelta(days=5),
            "PARTIAL": date(2026, 7, 1),
        }
        plan = build_1d_migration_plan(rows, existing, self.today, self.retention_days, max_chunk_days=90)
        self.assertEqual(len(plan.complete_tickers), 1)
        self.assertEqual(plan.complete_tickers[0].identity.ticker, "COMPLETE")
        self.assertEqual(len(plan.partial_history_tickers), 1)
        self.assertEqual(plan.partial_history_tickers[0].identity.ticker, "PARTIAL")
        self.assertEqual(len(plan.no_history_tickers), 1)
        self.assertEqual(plan.no_history_tickers[0].identity.ticker, "MISSING")

    def test_plan_is_deterministic_across_repeated_calls(self):
        rows = [{"ticker": "RELIANCE", "exchange": "NSE"}, {"ticker": "RELIANCE", "exchange": "BSE"}]
        existing = {"RELIANCE": date(2026, 7, 21)}
        first = build_1d_migration_plan(rows, existing, self.today, self.retention_days, max_chunk_days=90)
        second = build_1d_migration_plan(rows, existing, self.today, self.retention_days, max_chunk_days=90)
        self.assertEqual(
            [(p.identity.ticker, p.identity.exchange, p.fetch_range) for p in first.ticker_plans],
            [(p.identity.ticker, p.identity.exchange, p.fetch_range) for p in second.ticker_plans],
        )


if __name__ == "__main__":
    unittest.main()


class TestRuntimeEstimateRespectsGlobalRateLimit(unittest.TestCase):
    """The rate limiter is global (one shared RateLimiter inside the single
    AngelOneFetchManager that BatchDownloader creates), so worker count must
    NOT divide the runtime estimate -- it previously did, understating the
    full-universe run by the worker multiple."""

    def _plan(self, chunks):
        # total_chunks is a derived property, so subclass and override
        # estimate_requests rather than trying to assign to it.
        from migration.plan_1d import MigrationPlan

        class _FixedPlan(MigrationPlan):
            def __init__(self, n):
                self._n = n

            def estimate_requests(self):
                return self._n

        return _FixedPlan(chunks)

    def test_workers_do_not_reduce_estimated_runtime(self):
        plan = self._plan(8000)
        one = plan.estimate_runtime_seconds(0.3, 1)
        many = plan.estimate_runtime_seconds(0.3, 8)
        self.assertEqual(one, many, "workers cannot beat a global rate cap")

    def test_estimate_is_requests_over_rate(self):
        plan = self._plan(8063)
        self.assertAlmostEqual(plan.estimate_runtime_seconds(0.3, 2), 8063 / 0.3, places=3)

    def test_higher_rate_does_reduce_runtime(self):
        plan = self._plan(8000)
        self.assertLess(plan.estimate_runtime_seconds(1.0, 1),
                        plan.estimate_runtime_seconds(0.3, 1))
