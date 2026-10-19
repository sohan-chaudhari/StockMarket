"""A ticker the user just opened must not wait behind the background sweep backlog.

Reported: opening a ticker showed a gap, and it backfilled "a few minutes later".

Measured on production 2026-10-19: the persistent worker processes ONE job per 20 s
in market hours --

    main.py  _backfill_job_worker:  batch = 1 if market_open else ...
                                    await asyncio.sleep(20 if market_open else 15)

-- and 136 jobs were eligible at that moment, claimed strictly FIFO:

    backfill_jobs.claim:  ORDER BY COALESCE(next_attempt_at, created_at) ASC

A job enqueued because a user opened a chart therefore went to the BACK of that
backlog: ~136 x 20 s ~= 45 minutes of waiting, which is why the gap the user was
looking at repaired long after they had stopped watching (or only after switching
timeframe and back, which re-ran the load).

`enqueue()` now stamps a `chart_open` job an hour into the past -- the key `claim()`
already sorts by -- and applies the same promotion on CONFLICT so a ticker whose job
is merely queued/retry_wait/partially_complete is promoted too. The claim query, the
worker's pacing and the provider limiter are untouched, so this changes ORDER ONLY,
not how fast work is allowed to happen.
"""
import datetime
import inspect
import unittest
from datetime import datetime as dt, timedelta
from pathlib import Path
from unittest import mock

import backfill_jobs as bj

_MAIN_SRC = Path(__file__).resolve().parent.parent.joinpath("main.py").read_text(encoding="utf-8", errors="ignore")


class ChartOpenPrioritySourceTests(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(bj.enqueue)

    def test_chart_open_is_promoted_on_insert(self):
        self.assertIn("CASE WHEN :r = 'chart_open' THEN NOW() - INTERVAL '1 hour'", self.src,
                      "a freshly enqueued chart-open job must sort ahead of the backlog")

    def test_chart_open_is_promoted_on_conflict_too(self):
        head, sep, tail = self.src.partition("DO UPDATE")
        self.assertTrue(sep, "the idempotent conflict path must still exist")
        self.assertIn("'chart_open'", tail,
                      "an EXISTING open job must be promoted as well -- that is the common case, "
                      "since the sweep usually queued the ticker first")

    def test_only_chart_open_is_promoted(self):
        self.assertNotIn("'sweep'", self.src, "background sweep jobs must keep their place")
        self.assertIn("ELSE backfill_jobs.next_attempt_at", self.src,
                      "a non-chart_open conflict must leave the schedule alone")

    def test_claim_ordering_is_unchanged_so_promotion_means_first(self):
        src = inspect.getsource(bj.claim)
        self.assertIn("ORDER BY COALESCE(next_attempt_at, created_at) ASC", src,
                      "the promotion relies on this ordering key")
        self.assertIn("next_attempt_at IS NULL OR next_attempt_at <= NOW()", src,
                      "a past next_attempt_at stays claimable")
        self.assertIn("FOR UPDATE SKIP LOCKED", src, "multi-worker safety must survive")

    def test_the_worker_pacing_is_unchanged(self):
        """Documents WHY priority is needed: one job per 20 s in market hours."""
        self.assertIn("1 if market_open else", _MAIN_SRC)
        self.assertIn("asyncio.sleep(20 if market_open else", _MAIN_SRC)


class QueueOrderingSemanticsTests(unittest.TestCase):
    """`claim()` is Postgres-only (FOR UPDATE SKIP LOCKED / make_interval), so the
    intended effect is pinned by applying the SAME ordering key to simulated rows."""

    @staticmethod
    def _claim_order(jobs):
        return [j["ticker"] for j in sorted(
            jobs,
            key=lambda j: j["next_attempt_at"] if j["next_attempt_at"] is not None else j["created_at"],
        )]

    @staticmethod
    def _backlog(n=136):
        base = dt(2026, 10, 19, 11, 0)
        return [{"ticker": f"SWEEP{i}", "next_attempt_at": base + timedelta(seconds=i), "created_at": base}
                for i in range(n)]

    def test_the_offset_enqueue_actually_uses_puts_the_job_first(self):
        """Reads the REAL offset out of the shipped SQL, so weakening it (or dropping
        the CASE) cannot pass silently."""
        import re
        m = re.search(r"NOW\(\) - INTERVAL '(\d+) (\w+)'", inspect.getsource(bj.enqueue))
        self.assertIsNotNone(m, "enqueue must stamp chart_open jobs into the past")
        amount, unit = int(m.group(1)), m.group(2)
        offset = timedelta(**{unit if unit.endswith('s') else unit + 's': amount})
        base = dt(2026, 10, 19, 11, 30)
        jobs = self._backlog() + [{"ticker": "TATACAP", "next_attempt_at": base - offset, "created_at": base}]
        self.assertEqual(self._claim_order(jobs)[0], "TATACAP")

    def test_promoted_chart_open_job_is_claimed_before_the_whole_backlog(self):
        base = dt(2026, 10, 19, 11, 30)
        jobs = self._backlog() + [{"ticker": "TATACAP", "next_attempt_at": base - timedelta(hours=1),
                                   "created_at": base}]
        order = self._claim_order(jobs)
        self.assertEqual(order[0], "TATACAP", "the ticker the user opened is claimed next")
        self.assertEqual(len(order), 137)

    def test_without_the_promotion_it_waits_behind_the_backlog(self):
        """The pre-fix behaviour this fixes: last in, so ~45 minutes out."""
        base = dt(2026, 10, 19, 11, 30)
        jobs = self._backlog() + [{"ticker": "TATACAP", "next_attempt_at": base, "created_at": base}]
        order = self._claim_order(jobs)
        self.assertEqual(order[-1], "TATACAP")
        self.assertGreater(order.index("TATACAP"), 100)

    def test_a_sweep_job_keeps_its_place(self):
        base = dt(2026, 10, 19, 11, 0)
        jobs = [{"ticker": "A", "next_attempt_at": base, "created_at": base},
                {"ticker": "B", "next_attempt_at": base + timedelta(seconds=1), "created_at": base}]
        self.assertEqual(self._claim_order(jobs), ["A", "B"])

    def test_a_null_next_attempt_falls_back_to_created_at(self):
        base = dt(2026, 10, 19, 11, 0)
        jobs = [{"ticker": "OLD", "next_attempt_at": None, "created_at": base},
                {"ticker": "NEW", "next_attempt_at": None, "created_at": base + timedelta(seconds=5)}]
        self.assertEqual(self._claim_order(jobs), ["OLD", "NEW"])


class ProducerTests(unittest.TestCase):
    """The promotion is keyed on the reason string, so pin who sends which."""

    def test_only_the_chart_open_producer_uses_the_promoted_reason(self):
        self.assertIn('reason="chart_open"', _MAIN_SRC)
        self.assertIn('reason="sweep"', _MAIN_SRC)
        self.assertEqual(_MAIN_SRC.count('reason="chart_open"'), 1)
        self.assertEqual(_MAIN_SRC.count('reason="sweep"'), 1)


if __name__ == "__main__":
    unittest.main()
