"""RT-10 regression: CriticalIndexPoller must back off during a sustained
Angel One outage instead of hammering the REST API every ~1s forever.
Matches the circuit-breaker pattern already used by PricePoller.
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from price_poller import CriticalIndexPoller, CRITICAL_INDICES, CRITICAL_POLL_INTERVAL


class TestCriticalIndexPollerCircuitBreaker(unittest.TestCase):
    def _make_poller(self, poll_result):
        svc = MagicMock()
        svc.is_logged_in = True
        svc.smart_api = MagicMock()
        poller = CriticalIndexPoller(svc)
        for t in CRITICAL_INDICES:
            poller._token_cache[t] = {"exchange": "NSE", "symbol": t, "token": "1"}
        poller._poll_ticker = MagicMock(return_value=poll_result)
        return poller

    def _run_briefly(self, poller, condition, timeout=2.0):
        with patch("price_poller.time.sleep"):
            t = threading.Thread(target=poller._run_loop, daemon=True)
            t.start()
            deadline = time.time() + timeout
            while not condition() and time.time() < deadline:
                time.sleep(0.005)
            poller._stop_event.set()
            t.join(timeout=1)

    def test_backs_off_after_consecutive_failed_cycles(self):
        poller = self._make_poller(poll_result=False)

        self._run_briefly(
            poller,
            lambda: poller._consecutive_cycle_failures >= CriticalIndexPoller.MAX_CONSECUTIVE_FAILURES,
        )

        self.assertGreaterEqual(poller._consecutive_cycle_failures, CriticalIndexPoller.MAX_CONSECUTIVE_FAILURES)
        self.assertGreater(poller._current_interval, CRITICAL_POLL_INTERVAL)

    def test_stays_at_base_interval_when_succeeding(self):
        poller = self._make_poller(poll_result=True)

        self._run_briefly(poller, lambda: poller._stats["polls"] >= len(CRITICAL_INDICES) * 3)

        self.assertEqual(poller._consecutive_cycle_failures, 0)
        self.assertEqual(poller._current_interval, CRITICAL_POLL_INTERVAL)

    def test_recovers_to_base_interval_once_polling_succeeds_again(self):
        poller = self._make_poller(poll_result=False)

        self._run_briefly(
            poller,
            lambda: poller._consecutive_cycle_failures >= CriticalIndexPoller.MAX_CONSECUTIVE_FAILURES,
        )
        self.assertGreater(poller._current_interval, CRITICAL_POLL_INTERVAL)

        poller._stop_event.clear()
        poller._poll_ticker = MagicMock(return_value=True)
        self._run_briefly(poller, lambda: poller._consecutive_cycle_failures == 0)

        self.assertEqual(poller._consecutive_cycle_failures, 0)
        self.assertEqual(poller._current_interval, CRITICAL_POLL_INTERVAL)


if __name__ == "__main__":
    unittest.main()
