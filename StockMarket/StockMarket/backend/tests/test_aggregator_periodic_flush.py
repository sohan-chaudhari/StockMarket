"""RT-12 regression: aggregator.py's flush worker must periodically flush
_flush_batch even when no NEW candle completion triggers it.

SIGKILL/OOM can't be intercepted by any process-level code -- this doesn't
make a hard crash safe, it only bounds how long a completed candle can sit
buffered (and therefore at risk) during a quiet period where nothing else
is completing candles to trigger the normal push.
"""
import threading
import time
import unittest
from unittest.mock import MagicMock

from aggregator import Live5mBuilder


def _fake_candle(i):
    return {
        "ticker": f"T{i}", "timeframe": "5m", "timestamp": i,
        "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 100,
    }


class TestPeriodicFlush(unittest.TestCase):
    def setUp(self):
        self.builder = Live5mBuilder()

    def tearDown(self):
        self.builder._flush_worker_running = False

    def _run_worker_until(self, condition, timeout=2.0):
        self.builder._flush_worker_running = True
        t = threading.Thread(target=self.builder._flush_worker, daemon=True)
        t.start()
        deadline = time.time() + timeout
        while not condition() and time.time() < deadline:
            time.sleep(0.01)
        self.builder._flush_worker_running = False
        t.join(timeout=1)

    def test_stale_batch_gets_flushed_without_a_new_completion(self):
        self.builder._flush_batch_max_age = 0.05  # short, for a fast test
        self.builder._batch_add(_fake_candle(1))
        # Simulate the batch having sat untouched past the age cap.
        self.builder._last_batch_add_time = time.time() - 1.0
        self.builder.batch_flush_callback = MagicMock()

        self._run_worker_until(lambda: self.builder.batch_flush_callback.called)

        self.builder.batch_flush_callback.assert_called_once()
        self.assertEqual(len(self.builder._flush_batch), 0)

    def test_fresh_batch_is_not_flushed_before_the_age_cap(self):
        self.builder._flush_batch_max_age = 10.0  # long, so it must NOT fire
        self.builder._batch_add(_fake_candle(1))
        self.builder.batch_flush_callback = MagicMock()

        self.builder._flush_worker_running = True
        t = threading.Thread(target=self.builder._flush_worker, daemon=True)
        t.start()
        time.sleep(0.2)  # well under the 10s cap
        self.builder._flush_worker_running = False
        t.join(timeout=1)

        self.builder.batch_flush_callback.assert_not_called()
        self.assertEqual(len(self.builder._flush_batch), 1)

    def test_queued_work_is_still_prioritized_over_the_periodic_check(self):
        # A real candle-completion flush (via _flush_batch_now) must not be
        # starved by the periodic-check branch -- both paths ultimately call
        # the same callback, this just confirms normal flushing still works
        # with the periodic check present.
        self.builder._flush_batch_max_age = 999.0  # won't fire during this test
        self.builder._batch_add(_fake_candle(1))
        self.builder._flush_batch_now()
        self.builder.batch_flush_callback = MagicMock()

        self._run_worker_until(lambda: self.builder.batch_flush_callback.called)

        self.builder.batch_flush_callback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
