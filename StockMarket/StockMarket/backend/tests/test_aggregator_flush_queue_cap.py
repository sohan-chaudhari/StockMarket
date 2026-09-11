"""RT-09 regression: aggregator.py's DB-flush queue must be bounded.

Before this fix, _flush_queue had no size ceiling -- the module's own
docstring claims "ticks are never dropped, back-pressure pauses the WS
consumer instead," but nothing actually implemented that. A sustained
Postgres outage (the connection engine sets no connect timeout, so a hung
connection attempt can block the single flush worker indefinitely) would
grow the queue without limit until the process runs out of memory, losing
everything queued -- not just the oldest part of it.

These tests exercise _flush_batch_now() and _flush_worker() directly against
a real Live5mBuilder instance (no mocked internals), proving the queue now
stays within its candle-count cap by dropping the oldest batches first, and
that the worker's bookkeeping (_flush_queue_candle_count) stays correct
across a successful flush, a retried failure, and a dropped-after-max-
retries failure.
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


class TestFlushQueueCap(unittest.TestCase):
    def setUp(self):
        self.builder = Live5mBuilder()

    def tearDown(self):
        self.builder._flush_worker_running = False

    def _push_batch(self, n):
        for i in range(n):
            self.builder._batch_add(_fake_candle(i))
        self.builder._flush_batch_now()

    def test_queue_stays_under_cap_normally(self):
        self.builder._flush_queue_max_candles = 100
        self._push_batch(5)
        self._push_batch(5)
        self.assertEqual(self.builder._flush_queue_candle_count, 10)
        self.assertEqual(len(self.builder._flush_queue), 2)

    def test_oldest_batch_dropped_when_cap_exceeded(self):
        self.builder._flush_queue_max_candles = 5
        self._push_batch(3)  # batch #1, running total = 3
        first_fid = self.builder._flush_queue[0][0]
        self._push_batch(3)  # batch #2, running total = 6 > cap -> drop batch #1
        self.assertEqual(len(self.builder._flush_queue), 1)
        self.assertNotEqual(self.builder._flush_queue[0][0], first_fid)
        self.assertEqual(self.builder._flush_queue_candle_count, 3)
        self.assertNotIn(first_fid, self.builder._flush_retries)

    def test_newest_batch_kept_even_if_alone_exceeds_cap(self):
        self.builder._flush_queue_max_candles = 2
        self._push_batch(10)
        self.assertEqual(len(self.builder._flush_queue), 1)
        self.assertEqual(self.builder._flush_queue_candle_count, 10)

    def _run_worker_until(self, condition, timeout=2.0):
        self.builder._flush_worker_running = True
        t = threading.Thread(target=self.builder._flush_worker, daemon=True)
        t.start()
        deadline = time.time() + timeout
        while not condition() and time.time() < deadline:
            time.sleep(0.01)
        self.builder._flush_worker_running = False
        t.join(timeout=1)

    def test_worker_decrements_count_on_successful_flush(self):
        self.builder._flush_queue_max_candles = 1000
        self._push_batch(4)
        self.builder.batch_flush_callback = MagicMock()

        self._run_worker_until(lambda: not self.builder._flush_queue)

        self.assertEqual(self.builder._flush_queue_candle_count, 0)
        self.builder.batch_flush_callback.assert_called_once()

    def test_worker_requeues_count_correctly_on_transient_failure(self):
        self.builder._flush_queue_max_candles = 1000
        self._push_batch(4)
        calls = []

        def flaky(batch):
            calls.append(batch)
            if len(calls) < 2:
                raise Exception("simulated outage")

        self.builder.batch_flush_callback = flaky

        self._run_worker_until(lambda: len(calls) >= 2)

        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(self.builder._flush_queue_candle_count, 0)  # eventually succeeded

    def test_worker_drops_batch_after_max_retries_and_clears_count(self):
        self.builder._flush_queue_max_candles = 1000
        self._push_batch(4)
        self.builder.batch_flush_callback = MagicMock(side_effect=Exception("db down"))

        # Wait on the retry count itself, not queue emptiness -- the queue
        # is briefly empty between a failed attempt and its requeue, so
        # polling "not queue" can catch that window and stop the worker
        # after only 1 retry instead of letting all 3 play out.
        self._run_worker_until(lambda: self.builder.batch_flush_callback.call_count >= 3)
        # Give the worker one more loop iteration to act on the 3rd failure
        # (drop + stop re-appending) before asserting final state.
        time.sleep(0.05)

        self.assertEqual(len(self.builder._flush_queue), 0)
        self.assertEqual(self.builder._flush_queue_candle_count, 0)
        self.assertGreaterEqual(self.builder.batch_flush_callback.call_count, 3)


if __name__ == "__main__":
    unittest.main()
