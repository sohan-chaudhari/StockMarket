"""Regression tests for the delayed-tick and cumulative-volume fixes.

Root cause (both pre-existing in aggregator.py, fixed in this change):

  1. `process_tick` rewrote ANY exchange timestamp more than 30s behind the
     server clock to "now". A same-session tick delayed by >30s therefore
     mapped to the CURRENT 5m bucket and widened its high/low/close with a
     stale price, while the bucket it actually belonged to never received it.
  2. The cumulative-day-volume baseline (`_last_day_volume`) was overwritten on
     every tick, so an out-of-order / lower cumulative observation lowered the
     baseline and inflated the NEXT tick's delta.

Fixes:
  * a plausible timestamp is used AS-IS, and a tick whose true bucket is older
    than the ticker's current bucket is routed to `_route_late_tick` instead of
    the current candle. It widens only the *pending* candle's order-independent
    high/low (when that bucket is still inside the 60s late buffer), never the
    current candle, and never advances the ticker's timeline.
  * the volume baseline may only move DOWNWARD on a trading-date change (a real
    session reset); within a session a lower cumulative value contributes 0 and
    leaves the baseline untouched.

All tests use deterministic timestamps (the aggregator clock + session gates
are patched) and are DB-free: `_flush_batch_now` is neutralised so nothing is
queued or persisted.
"""
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from aggregator import Live5mBuilder, IST, snap_to_nse_session

TICKER = "ZZLATE"

# A fixed in-session trading instant: Fri 2026-10-09 10:22:00 IST.
FIXED_NOW = datetime(2026, 10, 9, 10, 22, 0)
FIXED_EPOCH = int(FIXED_NOW.replace(tzinfo=IST).timestamp())


def ep(h, m, s=0):
    """Epoch for a time on the fixed trading day (2026-10-09)."""
    return int(datetime(2026, 10, 9, h, m, s).replace(tzinfo=IST).timestamp())


def epoch_of(dt):
    return int(dt.replace(tzinfo=IST).timestamp())


class _Base(unittest.TestCase):
    """Patched clock + session gates; persistence neutralised."""

    now = FIXED_NOW

    def setUp(self):
        self.builder = Live5mBuilder()
        self.builder._flush_worker_running = False
        self.builder._flush_batch_now = lambda: None  # never queue/persist

    def tearDown(self):
        self.builder._flush_worker_running = False

    @contextmanager
    def clock(self, now=None):
        now = now or self.now
        with patch("aggregator.ist_now_naive", return_value=now), \
             patch("aggregator.time.time", return_value=float(epoch_of(now))), \
             patch("aggregator.is_market_hour", return_value=True), \
             patch("aggregator.is_trading_day", return_value=True):
            yield

    def tick(self, ts, price, day_volume=None, volume=0, ticker=TICKER):
        self.builder.process_tick(ticker, price, volume=volume, tick_ts=ts, day_volume=day_volume)

    def current(self, ticker=TICKER):
        return self.builder.get_current(ticker)

    def pending(self, ticker=TICKER):
        return self.builder._pending_mgr.get(ticker)

    @staticmethod
    def bucket_epoch(h, m):
        return ep(h, m)


class CurrentBucketUnchangedTests(_Base):
    def test_valid_current_bucket_tick_keeps_correct_ohlcv(self):
        with self.clock():
            self.tick(ep(10, 21, 0), 100.0, day_volume=1000)
            self.tick(ep(10, 22, 0), 101.0, day_volume=1500)
        cur = self.current()["5m"]
        self.assertEqual((cur["open"], cur["high"], cur["low"], cur["close"]),
                         (100.0, 101.0, 100.0, 101.0))
        self.assertEqual(cur["volume"], 500)
        self.assertEqual(cur["time"], ep(10, 20))


class DelayedTickInsidePendingWindowTests(_Base):
    """A >30s-delayed tick whose bucket is still pending must NOT touch the
    current candle; it may only widen that pending candle's high/low."""

    def _run(self):
        with self.clock():
            self.tick(ep(10, 19, 50), 100.0, day_volume=900)    # bucket 10:15 (forms it)
            self.tick(ep(10, 20, 10), 101.0, day_volume=1000)   # bucket 10:20 -> 10:15 becomes pending
            before_cur = dict(self.current()["5m"])
            before_pending = self.pending()
            self.assertIsNotNone(before_pending)
            self.tick(ep(10, 19, 55), 999.0, day_volume=950)    # LATE tick for bucket 10:15
        return before_cur, before_pending

    def test_late_tick_does_not_alter_the_current_candle(self):
        before_cur, _ = self._run()
        after_cur = self.current()["5m"]
        self.assertEqual(after_cur, before_cur,
                         "a late tick must not change the current 10:20 candle")
        self.assertNotEqual(after_cur["high"], 999.0)

    def test_late_tick_widens_only_the_pending_high_low(self):
        _, before_pending = self._run()
        p = self.pending()
        self.assertIsNotNone(p)
        self.assertEqual(p.bucket_start, datetime(2026, 10, 9, 10, 15))
        self.assertEqual(p.high, 999.0,
                         "the late price is a real trade in the 10:15 bucket -> widen high")
        self.assertEqual(p.low, 100.0, "low unchanged (999.0 is not below it)")
        # open/close are arrival-ordered and deliberately NOT rewritten
        self.assertEqual(p.open, before_pending.open)
        self.assertEqual(p.close, before_pending.close)

    def test_late_tick_does_not_move_the_timeline_backwards(self):
        self._run()
        self.assertEqual(self.builder._last_known_bucket[TICKER]["5m"], ep(10, 20))


class DelayedTickAfterPendingFlushedTests(_Base):
    """Once the pending candle has been flushed, a late tick must be ignored."""

    def test_late_tick_after_flush_cannot_contaminate_current(self):
        with self.clock():
            self.tick(ep(10, 19, 50), 100.0, day_volume=900)    # bucket 10:15
            self.tick(ep(10, 20, 10), 101.0, day_volume=1000)   # bucket 10:20; 10:15 -> pending
            # simulate the 60s late buffer expiring (pending flushed to DB)
            self.builder._pending_mgr.get_expired(FIXED_EPOCH + 61)
            self.assertIsNone(self.pending())
            before_cur = dict(self.current()["5m"])
            self.tick(ep(10, 19, 55), 999.0, day_volume=950)    # late tick, no pending left
        self.assertEqual(self.current()["5m"], before_cur)
        self.assertNotEqual(self.current()["5m"]["high"], 999.0)


class OutOfOrderVolumeTests(_Base):
    def test_lower_cumulative_value_does_not_inflate_next_delta(self):
        with self.clock():
            self.tick(ep(10, 22, 0), 101.0, day_volume=2000)    # baseline 2000, +0
            self.tick(ep(10, 22, 10), 101.0, day_volume=1500)   # lower, later ts -> +0, baseline STAYS 2000
            self.tick(ep(10, 22, 20), 101.0, day_volume=2500)   # 2500-2000 = +500
        self.assertEqual(self.current()["5m"]["volume"], 500)
        self.assertEqual(self.builder._last_day_volume[TICKER], 2500)

    def test_older_timestamp_out_of_order_tick_is_not_applied(self):
        with self.clock():
            self.tick(ep(10, 22, 0), 101.0, day_volume=2000)
            self.tick(ep(10, 21, 0), 500.0, day_volume=1500)    # older ts, same bucket -> dropped
            self.tick(ep(10, 22, 10), 101.0, day_volume=2500)
        cur = self.current()["5m"]
        self.assertEqual(cur["volume"], 500)
        self.assertEqual(cur["high"], 101.0, "the stale 500.0 price must not enter the bucket")
        self.assertEqual(self.builder._last_day_volume[TICKER], 2500)

    def test_exact_repeat_is_idempotent(self):
        with self.clock():
            self.tick(ep(10, 22, 0), 101.0, day_volume=2000)
            self.tick(ep(10, 22, 1), 101.0, day_volume=2000)
            self.tick(ep(10, 22, 2), 101.0, day_volume=2000)
        self.assertEqual(self.current()["5m"]["volume"], 0)

    def test_normal_increasing_volume_deltas(self):
        with self.clock():
            self.tick(ep(10, 22, 0), 100.0, day_volume=1000)
            self.tick(ep(10, 22, 1), 100.5, day_volume=1400)
            self.tick(ep(10, 22, 2), 101.0, day_volume=1900)
        self.assertEqual(self.current()["5m"]["volume"], 900)

    def test_legacy_per_tick_volume_still_works(self):
        with self.clock():
            self.tick(ep(10, 22, 0), 100.0, volume=100)
            self.tick(ep(10, 22, 1), 100.0, volume=50)
        self.assertEqual(self.current()["5m"]["volume"], 150)

    def test_session_reset_still_resets_the_baseline(self):
        with self.clock():
            self.tick(ep(10, 22, 0), 100.0, day_volume=5_000_000)
            self.tick(ep(10, 22, 1), 100.0, day_volume=5_000_000)
        self.assertEqual(self.current()["5m"]["volume"], 0)
        # next trading day: the cumulative counter resets small
        nxt = datetime(2026, 10, 12, 10, 0, 0)  # Monday
        with self.clock(now=nxt):
            nxt_ts = epoch_of(nxt)
            self.builder.process_tick(TICKER, 100.0, tick_ts=nxt_ts, day_volume=2000)
            self.builder.process_tick(TICKER, 100.0, tick_ts=nxt_ts + 1, day_volume=5000)
        self.assertEqual(self.current()["5m"]["volume"], 3000)


class TimestampHandlingTests(_Base):
    def test_missing_timestamp_uses_arrival_time(self):
        with self.clock():
            self.builder.process_tick(TICKER, 100.0, tick_ts=None, day_volume=1000)
        self.assertEqual(self.current()["5m"]["time"], ep(10, 20))

    def test_invalid_timestamp_uses_arrival_time(self):
        with self.clock():
            self.builder.process_tick(TICKER, 100.0, tick_ts=0, day_volume=1000)
            self.builder.process_tick(TICKER, 101.0, tick_ts=123, day_volume=1100)
        self.assertEqual(self.current()["5m"]["time"], ep(10, 20))

    def test_future_timestamp_is_clamped_to_arrival(self):
        with self.clock():
            self.builder.process_tick(TICKER, 100.0, tick_ts=FIXED_EPOCH + 600, day_volume=1000)
        self.assertEqual(self.current()["5m"]["time"], ep(10, 20))

    def test_reconnect_burst_within_one_bucket_is_applied(self):
        # All ticks belong to the 10:20 bucket; a burst after a brief stall must
        # still build the bucket correctly (no tick dropped for being >30s old).
        with self.clock():
            for ts, px, dv in [
                (ep(10, 20, 0), 100.0, 1000),
                (ep(10, 20, 30), 100.2, 1100),
                (ep(10, 21, 0), 100.4, 1200),
                (ep(10, 21, 30), 100.6, 1300),
                (ep(10, 22, 0), 100.8, 1400),
            ]:
                self.tick(ts, px, day_volume=dv)
        cur = self.current()["5m"]
        self.assertEqual(cur["high"], 100.8)
        self.assertEqual(cur["low"], 100.0)
        self.assertEqual(cur["volume"], 400)


class SessionBoundaryTests(_Base):
    def test_grace_window_tick_maps_to_last_real_bucket(self):
        # 15:29 belongs to the 15:25 bucket; the 15:30-15:45 grace must not
        # create a phantom 15:30 bucket.
        late_now = datetime(2026, 10, 9, 15, 35, 0)
        with self.clock(now=late_now):
            self.builder.process_tick(TICKER, 100.0, tick_ts=ep(15, 29, 0), day_volume=1000)
        self.assertEqual(self.current()["5m"]["time"], ep(15, 25))

    def test_same_bucket_grace_tick_updates_normally(self):
        late_now = datetime(2026, 10, 9, 15, 35, 0)
        with self.clock(now=late_now):
            self.builder.process_tick(TICKER, 100.0, tick_ts=ep(15, 26, 0), day_volume=1000)
            self.builder.process_tick(TICKER, 101.0, tick_ts=ep(15, 29, 0), day_volume=1200)
        cur = self.current()["5m"]
        self.assertEqual(cur["close"], 101.0)
        self.assertEqual(cur["time"], ep(15, 25))


if __name__ == "__main__":
    unittest.main()
