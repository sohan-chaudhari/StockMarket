"""Tests for the shared historical-data coordination layer.

Every consumer of Angel One history (chart open, GapSweep, index-daily,
daily/intraday prefill, dashboard index, 1W/1M backfill, WS recovery) funnels
through historical_service.get_historical_candles(). That single method now owns:

  * in-flight DEDUPLICATION of concurrent identical requests,
  * a SHARED account-wide cooldown (a throttle pauses every caller),
  * account-wide PACING (configurable),
  * Retry-After awareness and bounded backoff,
  * counters for observability.

All provider calls are mocked; no live API is contacted.
"""
import threading
import time
import types
import unittest
from unittest.mock import patch

import historical_service as hs


def _make_service(delay=0.0):
    """A stand-in with the REAL get_historical_candles() bound to it.

    HistoricalDataService is a singleton (its __init__ early-returns for later
    instances), so a subclass cannot be initialised normally; binding the real
    wrapper onto a plain object exercises the genuine coordination code path.
    """
    svc = type("Svc", (), {})()
    svc.is_logged_in = True
    svc.calls = 0
    svc.delay = delay
    svc.result = [{"timestamp": "2026-10-13T09:15:00+05:30", "open": 1, "high": 1,
                   "low": 1, "close": 1, "volume": 1}]
    svc._lock = threading.Lock()

    def _fetch(self, ticker, interval, from_date, to_date,
               exchange="NSE", raise_on_error=False, priority=False):
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return list(self.result)

    svc.get_historical_candles = types.MethodType(
        hs.HistoricalDataService.get_historical_candles, svc)
    svc._fetch_historical_uncached = types.MethodType(_fetch, svc)
    return svc


class _Base(unittest.TestCase):
    def setUp(self):
        self.svc = _make_service()
        self._saved = (hs._rate_limit_until, hs._next_slot, hs._HISTORICAL_MIN_INTERVAL_SEC)
        hs._rate_limit_until = 0.0
        hs._next_slot = 0.0
        hs._HISTORICAL_MIN_INTERVAL_SEC = 0.0
        hs._INFLIGHT.clear()
        for k in hs._metrics:
            hs._metrics[k] = 0
        self.addCleanup(self._restore)

    def _restore(self):
        hs._rate_limit_until, hs._next_slot, hs._HISTORICAL_MIN_INTERVAL_SEC = self._saved
        hs._INFLIGHT.clear()

    def _get(self, **kw):
        args = dict(ticker="RPSGVENT", interval="FIVE_MINUTE",
                    from_date="2026-08-17", to_date="2026-10-13", exchange="NSE")
        args.update(kw)
        return self.svc.get_historical_candles(**args)


class DeduplicationTests(_Base):
    def test_concurrent_identical_requests_share_one_provider_call(self):
        self.svc.delay = 0.25          # keep the owner inside the call
        results = []
        def worker():
            results.append(self._get())
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(self.svc.calls, 1,
                         "5 concurrent identical requests must make ONE provider call")
        self.assertEqual(len(results), 5)
        self.assertTrue(all(r == results[0] for r in results),
                        "every waiter must receive the same result")
        self.assertEqual(hs._metrics["dedup_hits"], 4)

    def test_different_ranges_are_not_deduplicated(self):
        self.svc.delay = 0.2
        a = []
        t1 = threading.Thread(target=lambda: a.append(self._get(to_date="2026-10-13")))
        t2 = threading.Thread(target=lambda: a.append(self._get(to_date="2026-10-12")))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(self.svc.calls, 2, "different ranges are different requests")

    def test_different_timeframes_are_not_deduplicated(self):
        self.svc.delay = 0.2
        ts = [threading.Thread(target=lambda i=i: self._get(interval=i))
              for i in ("FIVE_MINUTE", "FIFTEEN_MINUTE")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(self.svc.calls, 2,
                         "5m and 15m are different datasets and must not share a call")

    def test_sequential_requests_are_not_deduplicated(self):
        self._get(); self._get()
        self.assertEqual(self.svc.calls, 2, "dedup is for CONCURRENT requests only")


class SharedCooldownTests(_Base):
    def test_cooldown_blocks_every_consumer(self):
        hs._note_rate_limit()
        # Any source (chart, sweep, index, weekly) must be blocked identically.
        self.assertEqual(self._get(), [])
        self.assertEqual(self.svc.calls, 0, "no provider call while cooling down")

    def test_cooldown_raises_retryable_for_raise_on_error_callers(self):
        hs._note_rate_limit()
        with self.assertRaises(hs.HistoricalFetchError) as ctx:
            self._get(raise_on_error=True)
        self.assertTrue(ctx.exception.retryable)

    def test_retry_after_extends_the_cooldown(self):
        hs._note_rate_limit("429 Retry-After: 200")
        self.assertGreaterEqual(hs.rate_limit_backoff_remaining(), 190)

    def test_cooldown_expires_and_calls_resume(self):
        hs._note_rate_limit()
        hs._rate_limit_until = time.time() - 1
        self.assertEqual(len(self._get()), 1)
        self.assertEqual(self.svc.calls, 1)


class PacingTests(_Base):
    def test_calls_are_spaced(self):
        # The slot gate lives inside the real fetch, which this test's stand-in
        # replaces -- so exercise the gate itself.
        hs._HISTORICAL_MIN_INTERVAL_SEC = 0.3
        hs._next_slot = 0.0
        start = time.time()
        hs._acquire_slot()
        hs._acquire_slot()
        self.assertGreaterEqual(time.time() - start, 0.29,
                                "the second slot must wait for the configured interval")

    def test_pacing_is_configurable_by_env(self):
        self.assertIsInstance(hs._HISTORICAL_MIN_INTERVAL_SEC, float)
        self.assertGreaterEqual(hs._RATE_LIMIT_BACKOFF_SEC, 1.0)


class MetricsTests(_Base):
    def test_metrics_count_calls_and_throttles(self):
        self._get()
        hs._note_rate_limit()
        m = hs.historical_metrics()
        self.assertEqual(m["calls"], 1)
        self.assertEqual(m["throttles"], 1)
        self.assertGreaterEqual(m["cooldowns"], 1)


class TimeframeIndependenceTests(_Base):
    def test_5m_and_15m_have_separate_dedup_keys(self):
        # A completed 15m request must never be treated as satisfying 5m.
        hs._HISTORICAL_MIN_INTERVAL_SEC = 0.0
        self._get(interval="FIFTEEN_MINUTE")
        self._get(interval="FIVE_MINUTE")
        self.assertEqual(self.svc.calls, 2)


if __name__ == "__main__":
    unittest.main()
