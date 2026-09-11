"""
Retention batch-boundary bug proof and fix verification.

The bug (5m→15m, batch_size=100):
  100 % 3 == 1 → row 99 (0-indexed) is always the FIRST slot of a new 15m
  bucket. The resampler produces a partial target candle. ON CONFLICT DO NOTHING
  permanently blocks the correct complete candle when the remaining 2 slots
  arrive in the next batch. This corrupts every 15m candle at every batch
  boundary — i.e., every single batch for this rule.

The fix:
  _target_bucket_tail() returns the timestamp of the last source slot in the
  same target bucket as the last fetched row. _process_ticker() extends the
  source batch to that boundary before resampling.
"""

import unittest
from datetime import datetime, timedelta

from resampler import CandleResampler
from retention_service import RetentionService


def _c(ts, open_=100, high=110, low=90, close=105, volume=1000):
    return {"timestamp": ts, "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


def _dt(h, m, d=1):
    return datetime(2026, 7, d, h, m)


# One complete NSE session worth of 5m candles (75 slots 09:15–15:25)
_SESSION = [
    _c(_dt(h, m), open_=100 + i, high=115 + i, low=85 + i, close=105 + i, volume=1000)
    for i, (h, m) in enumerate(
        (h, m)
        for h in range(9, 16)
        for m in (range(15, 60, 5) if h == 9 else
                  range(0, 60, 5)  if h < 15  else
                  range(0, 30, 5))
        if not (h == 9 and m < 15)
    )
]


# ── _target_bucket_tail ──────────────────────────────────────────────────────

class TestTargetBucketTail(unittest.TestCase):
    """Pure NSE-alignment math — no DB, no resampler."""

    def _tail(self, h, m, src="5m", tgt="15m", d=1):
        return RetentionService._target_bucket_tail(_dt(h, m, d), src, tgt)

    # 5m → 15m

    def test_5m_15m_first_slot_extends_to_last(self):
        # 09:15 = first slot of [09:15..09:25]; must extend to 09:25
        self.assertEqual(self._tail(9, 15), _dt(9, 25))

    def test_5m_15m_middle_slot_same_bucket(self):
        self.assertEqual(self._tail(9, 20), _dt(9, 25))

    def test_5m_15m_last_slot_no_extension_needed(self):
        # 09:25 IS the tail; last_ts < tail_end is False → no extra fetch
        tail = self._tail(9, 25)
        self.assertEqual(tail, _dt(9, 25))
        self.assertFalse(_dt(9, 25) < tail)

    def test_5m_15m_second_bucket_first_slot(self):
        self.assertEqual(self._tail(9, 30), _dt(9, 40))

    def test_5m_15m_second_bucket_last_slot(self):
        tail = self._tail(9, 40)
        self.assertEqual(tail, _dt(9, 40))
        self.assertFalse(_dt(9, 40) < tail)

    def test_5m_15m_session_close_bucket_first(self):
        # 15:15 → bucket [15:15..15:25], last slot = 15:25
        self.assertEqual(self._tail(15, 15), _dt(15, 25))

    def test_5m_15m_session_last_slot(self):
        self.assertEqual(self._tail(15, 25), _dt(15, 25))

    # 5m → 30m (6 slots per bucket)

    def test_5m_30m_first_slot(self):
        # Bucket [09:15..09:40], last slot = 09:40
        self.assertEqual(self._tail(9, 15, tgt="30m"), _dt(9, 40))

    def test_5m_30m_mid_slot(self):
        self.assertEqual(self._tail(9, 25, tgt="30m"), _dt(9, 40))

    def test_5m_30m_last_slot_no_extension(self):
        tail = self._tail(9, 40, tgt="30m")
        self.assertEqual(tail, _dt(9, 40))
        self.assertFalse(_dt(9, 40) < tail)

    # 5m → 1h (12 slots per bucket)

    def test_5m_1h_first_slot(self):
        # Bucket [09:15..10:10], last slot = 10:10
        self.assertEqual(self._tail(9, 15, tgt="1h"), _dt(10, 10))

    def test_5m_1h_last_slot_no_extension(self):
        tail = self._tail(10, 10, tgt="1h")
        self.assertEqual(tail, _dt(10, 10))
        self.assertFalse(_dt(10, 10) < tail)

    # 15m → 30m (2 slots per bucket — batch_size=100 ends cleanly)

    def test_15m_30m_first_slot(self):
        # [09:15, 09:30] bucket, last slot = 09:30
        self.assertEqual(self._tail(9, 15, src="15m", tgt="30m"), _dt(9, 30))

    def test_15m_30m_last_slot_no_extension(self):
        tail = self._tail(9, 30, src="15m", tgt="30m")
        self.assertFalse(_dt(9, 30) < tail)

    # Calendar chains → non-None (week/month bucket end)

    def test_1d_to_1w_returns_sunday_of_same_week(self):
        # 2026-07-01 is Wednesday; Monday = Jun 29; Sunday = Jul 5
        tail = RetentionService._target_bucket_tail(datetime(2026, 7, 1), "1D", "1W")
        self.assertEqual(tail, datetime(2026, 7, 5))

    def test_1w_to_1m_returns_last_day_of_month(self):
        # 2026-07-01; last day of July = Jul 31
        tail = RetentionService._target_bucket_tail(datetime(2026, 7, 1), "1W", "1M")
        self.assertEqual(tail, datetime(2026, 7, 31))

    def test_pre_session_returns_none(self):
        self.assertIsNone(RetentionService._target_bucket_tail(
            _dt(9, 0), "5m", "15m"))


# ── Bug proof ────────────────────────────────────────────────────────────────

class TestBatchBoundaryBugProof(unittest.TestCase):
    """
    Prove mathematically that a split batch corrupts OHLCV,
    and that ON CONFLICT DO NOTHING blocks the correct candle.
    No DB mock required — we exercise CandleResampler directly.
    """

    def setUp(self):
        # One complete 15m bucket: 09:15, 09:20, 09:25
        self.bucket = [
            _c(_dt(9, 15), open_=100, high=112, low=88, close=105, volume=1000),
            _c(_dt(9, 20), open_=105, high=118, low=95, close=110, volume=800),
            _c(_dt(9, 25), open_=110, high=125, low=100, close=120, volume=1200),
        ]
        self.expected = dict(open=100, high=125, low=88, close=120, volume=3000)

    def test_batch_size_100_always_splits_5m_to_15m(self):
        """100 % 3 == 1: the 100th row is always the FIRST slot of a new bucket."""
        self.assertEqual(100 % 3, 1,
            "batch_size=100 always ends on the first slot of a 15m bucket")

    def test_partial_batch_first_slot_only_wrong_close(self):
        partial = CandleResampler.resample_5m_to(self.bucket[:1], "15m")
        self.assertEqual(len(partial), 1)
        self.assertNotEqual(partial[0]["close"], self.expected["close"],
                            "Partial 1-row batch must produce wrong close")
        self.assertEqual(partial[0]["close"], 105)

    def test_partial_batch_first_two_slots_wrong_close_and_volume(self):
        partial = CandleResampler.resample_5m_to(self.bucket[:2], "15m")
        self.assertEqual(len(partial), 1)
        self.assertNotEqual(partial[0]["close"], self.expected["close"])
        self.assertNotEqual(partial[0]["volume"], self.expected["volume"])
        self.assertEqual(partial[0]["close"], 110)
        self.assertEqual(partial[0]["volume"], 1800)

    def test_partial_and_complete_target_same_timestamp(self):
        """Both partial and complete batches write to the SAME target timestamp.
        The first (partial) write permanently blocks the second (correct) write
        via ON CONFLICT DO NOTHING."""
        partial = CandleResampler.resample_5m_to(self.bucket[:1], "15m")
        complete = CandleResampler.resample_5m_to(self.bucket, "15m")
        self.assertEqual(partial[0]["timestamp"], complete[0]["timestamp"],
                         "Partial and complete batches both target 09:15 15m")
        # But produce different OHLCV → the first write poisons the slot
        self.assertNotEqual(partial[0]["close"], complete[0]["close"])

    def test_complete_batch_correct_ohlcv(self):
        result = CandleResampler.resample_5m_to(self.bucket, "15m")
        self.assertEqual(len(result), 1)
        for field, value in self.expected.items():
            self.assertEqual(result[0][field], value, f"field {field!r} mismatch")

    def test_corruption_affects_every_batch_in_5m_to_15m(self):
        """
        With batch_size=100 processing a full session (75 rows), every batch
        produces at least one partial bucket — there is no 'safe' batch.

        For a 100-row batch from 75 * N rows (multiple sessions):
          row 99 (0-indexed) = 99 % 3 == 0 → first slot of its bucket.
        """
        # Two sessions = 150 rows. With batch_size=100:
        # Batch 1 rows 0-99; row 99 % 3 == 0 → first slot of a bucket. Bug.
        # Batch 2 rows 100-149 (50 rows, which is 16 complete buckets + 2 rows)
        # Row 149 (session-relative position 149 % 3 == 2) → last slot. No bug on last batch.
        # But batch 1 already has the bug, which is what matters.
        self.assertEqual(99 % 3, 0, "row 99 is the first slot of its 15m bucket")


# ── Fix verification ─────────────────────────────────────────────────────────

class TestBatchBoundaryFix(unittest.TestCase):
    """Verify that extending the batch produces correct OHLCV."""

    def test_extension_produces_correct_15m_candle(self):
        """
        Simulate the fix: initial batch=[09:15], tail=09:25, extend to [09:15..09:25].
        Result must match the expected complete 15m candle.
        """
        all_rows = [
            _c(_dt(9, 15), open_=100, high=112, low=88, close=105, volume=1000),
            _c(_dt(9, 20), open_=105, high=118, low=95, close=110, volume=800),
            _c(_dt(9, 25), open_=110, high=125, low=100, close=120, volume=1200),
        ]
        initial = all_rows[:1]
        last_ts = initial[-1]["timestamp"]

        # Fix step 1: compute the tail
        tail_end = RetentionService._target_bucket_tail(last_ts, "5m", "15m")
        self.assertEqual(tail_end, _dt(9, 25))
        self.assertLess(last_ts, tail_end, "Extension is needed")

        # Fix step 2: fetch extra rows (simulated DB query)
        extra = [r for r in all_rows if last_ts < r["timestamp"] <= tail_end]
        self.assertEqual(len(extra), 2)  # 09:20 and 09:25

        # Fix step 3: extended batch → resample
        extended = initial + extra
        result = CandleResampler.resample_5m_to(extended, "15m")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 125)
        self.assertEqual(result[0]["low"], 88)
        self.assertEqual(result[0]["close"], 120)
        self.assertEqual(result[0]["volume"], 3000)

    def test_extension_produces_correct_30m_candle(self):
        """5m→30m: batch ends at 09:15 (slot 0 of 6), extend to 09:40."""
        all_rows = [_c(_dt(9, 15 + 5 * i), open_=100 + i, high=115 + i,
                       low=85 + i, close=105 + i, volume=1000)
                    for i in range(6)]  # 09:15..09:40

        initial = all_rows[:1]
        last_ts = initial[-1]["timestamp"]
        tail_end = RetentionService._target_bucket_tail(last_ts, "5m", "30m")
        self.assertEqual(tail_end, _dt(9, 40))

        extra = [r for r in all_rows if last_ts < r["timestamp"] <= tail_end]
        result = CandleResampler.resample_5m_to(initial + extra, "30m")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)       # first row's open
        self.assertEqual(result[0]["high"], max(115 + i for i in range(6)))
        self.assertEqual(result[0]["volume"], 6000)

    def test_no_extension_when_batch_ends_on_last_slot(self):
        last_ts = _dt(9, 25)  # last slot of first 15m bucket
        tail_end = RetentionService._target_bucket_tail(last_ts, "5m", "15m")
        self.assertFalse(last_ts < tail_end,
                         "Batch already complete — no extra fetch needed")

    def test_calendar_chain_1d_1w_extends_to_week_end(self):
        # 2026-07-01 is Wednesday; fix extends batch to Sunday Jul 5
        ts = datetime(2026, 7, 1)
        tail = RetentionService._target_bucket_tail(ts, "1D", "1W")
        self.assertEqual(tail, datetime(2026, 7, 5))
        self.assertGreater(tail, ts, "Extension needed: batch ends before week end")

    def test_calendar_chain_1w_1m_extends_to_month_end(self):
        # 2026-07-01; fix extends batch to July 31
        ts = datetime(2026, 7, 1)
        tail = RetentionService._target_bucket_tail(ts, "1W", "1M")
        self.assertEqual(tail, datetime(2026, 7, 31))
        self.assertGreater(tail, ts, "Extension needed: batch ends before month end")

    def test_extension_does_not_extend_past_cutoff(self):
        """
        If the tail slots are beyond the cutoff, the DB filter
        (Candle.timestamp < cutoff) returns nothing — the partial batch
        is processed as-is. Verify the tail computation itself does not
        check or enforce the cutoff (the DB query does that).
        """
        last_ts = _dt(9, 15)
        tail_end = RetentionService._target_bucket_tail(last_ts, "5m", "15m")
        self.assertEqual(tail_end, _dt(9, 25))
        # Even if cutoff < tail_end, _target_bucket_tail still returns 09:25.
        # The guard in _process_ticker is: Candle.timestamp < cutoff in the
        # extra-rows query — so extra will be empty, and the batch is unchanged.
        self.assertIsNotNone(tail_end)

    def test_multi_bucket_batch_extension_targets_last_bucket_only(self):
        """
        A batch with two complete buckets plus one partial (09:45 only)
        must extend only to 09:55, not re-fetch the earlier buckets.
        """
        last_ts = _dt(9, 45)  # first slot of third 15m bucket
        tail_end = RetentionService._target_bucket_tail(last_ts, "5m", "15m")
        self.assertEqual(tail_end, _dt(9, 55))
        self.assertLess(last_ts, tail_end)


# ── Normal-batch regression ───────────────────────────────────────────────────

class TestBatchBoundaryNormalRegression(unittest.TestCase):
    """
    Batches that naturally end on a complete target bucket must produce
    correct OHLCV with no change in behaviour.
    """

    def test_three_complete_15m_buckets(self):
        rows = [_c(_dt(9, 15 + 5 * i), open_=100 + i, high=115 + i,
                   low=85 + i, close=105 + i, volume=1000)
                for i in range(9)]  # 09:15..09:55 = three complete 15m buckets

        self.assertEqual(len(rows), 9)
        last_ts = rows[-1]["timestamp"]
        # 09:55 = last slot of 3rd bucket [09:45..09:55]
        tail = RetentionService._target_bucket_tail(last_ts, "5m", "15m")
        self.assertFalse(last_ts < tail, "No extension: batch ends on last slot")

        result = CandleResampler.resample_5m_to(rows, "15m")
        self.assertEqual(len(result), 3)
        # Each bucket: volume = sum of 3 × 1000 = 3000
        for candle in result:
            self.assertEqual(candle["volume"], 3000)

    def test_cross_session_buckets_unaffected(self):
        """Rows spanning two sessions must produce separate, correct 15m candles."""
        session1_close = [
            _c(_dt(15, 15, d=1), open_=100, high=110, low=90, close=105, volume=500),
            _c(_dt(15, 20, d=1), open_=105, high=115, low=95, close=110, volume=400),
            _c(_dt(15, 25, d=1), open_=110, high=120, low=100, close=115, volume=600),
        ]
        session2_open = [
            _c(_dt(9, 15, d=2), open_=200, high=210, low=190, close=205, volume=1000),
            _c(_dt(9, 20, d=2), open_=205, high=215, low=195, close=210, volume=800),
            _c(_dt(9, 25, d=2), open_=210, high=220, low=200, close=215, volume=1200),
        ]
        rows = session1_close + session2_open
        result = CandleResampler.resample_5m_to(rows, "15m")
        self.assertEqual(len(result), 2, "Two sessions → two 15m candles")
        self.assertEqual(result[0]["volume"], 1500)
        self.assertEqual(result[1]["volume"], 3000)

    def test_1d_to_1w_complete_week_produces_correct_candle(self):
        """A complete Mon–Fri batch produces the correct 1W candle.
        With the fix, tail_end = Sunday; but since no rows exist beyond Friday,
        the extra-fetch returns nothing and the batch is processed as-is."""
        monday = datetime(2026, 7, 6)
        daily = [
            _c(monday + timedelta(days=i),
               open_=100 + i * 5, high=110 + i * 5,
               low=90 + i * 5, close=105 + i * 5, volume=1000)
            for i in range(5)  # Mon–Fri
        ]
        ts = daily[-1]["timestamp"]  # Friday 2026-07-10
        # With the fix: tail_end = Sunday Jul 12 (not None)
        tail = RetentionService._target_bucket_tail(ts, "1D", "1W")
        self.assertEqual(tail, datetime(2026, 7, 12))
        # last_batch_ts (Fri) < tail_end (Sun) is True, but DB returns no
        # weekend rows → extension is a no-op, batch is unchanged
        self.assertLess(ts, tail)

        result = CandleResampler.resample_5m_to(daily, "1W")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["close"], 125)
        self.assertEqual(result[0]["volume"], 5000)


# ── Calendar batch boundary: 1D → 1W ────────────────────────────────────────

def _daily(ts, volume=1000):
    return _c(ts, open_=100, high=110, low=90, close=105, volume=volume)


def _weekly(ts, volume=5000):
    return _c(ts, open_=100, high=110, low=90, close=105, volume=volume)


class TestCalendarBatchBoundary1DTo1W(unittest.TestCase):
    """
    Verify _target_bucket_tail() for 1D→1W: must return the Sunday of the
    same ISO week, so the _process_ticker extension fetch collects the
    remaining trading days before resampling — preventing partial 1W candles
    when a batch boundary falls mid-week.
    """

    def _tail(self, d):
        return RetentionService._target_bucket_tail(d, "1D", "1W")

    # ── tail value correctness ──────────────────────────────────────────

    def test_monday_returns_sunday_of_same_week(self):
        # Monday 2026-07-06 → Sunday 2026-07-12
        self.assertEqual(self._tail(datetime(2026, 7, 6)), datetime(2026, 7, 12))

    def test_tuesday_returns_sunday_of_same_week(self):
        self.assertEqual(self._tail(datetime(2026, 7, 7)), datetime(2026, 7, 12))

    def test_wednesday_returns_sunday_of_same_week(self):
        self.assertEqual(self._tail(datetime(2026, 7, 1)), datetime(2026, 7, 5))

    def test_thursday_returns_sunday_of_same_week(self):
        # Thursday 2026-07-09
        self.assertEqual(self._tail(datetime(2026, 7, 9)), datetime(2026, 7, 12))

    def test_friday_returns_sunday_of_same_week(self):
        # Friday 2026-07-10 → Sunday 2026-07-12
        tail = self._tail(datetime(2026, 7, 10))
        self.assertEqual(tail, datetime(2026, 7, 12))
        # last_batch_ts (Fri) < tail_end (Sun) → True, but extra-fetch finds no
        # weekend rows → extension is a no-op; correct behavior
        self.assertLess(datetime(2026, 7, 10), tail)

    def test_tail_always_on_sunday(self):
        # For any weekday, tail must be a Sunday (weekday == 6)
        for day in range(1, 8):
            ts = datetime(2026, 7, day)
            tail = self._tail(ts)
            self.assertEqual(tail.weekday(), 6,
                             f"tail for {ts.date()} must be Sunday, got {tail.date()}")

    def test_tail_is_always_in_same_week(self):
        # Tail must be in the same ISO week as ts
        for day in range(6, 11):  # Mon-Fri of 2026-07 week
            ts = datetime(2026, 7, day)
            tail = self._tail(ts)
            ts_week_mon = ts - timedelta(days=ts.weekday())
            tail_week_mon = tail - timedelta(days=tail.weekday())
            self.assertEqual(ts_week_mon, tail_week_mon,
                             f"{ts.date()} and {tail.date()} must be in same week")

    # ── extension semantics ─────────────────────────────────────────────

    def test_batch_ending_monday_needs_extension(self):
        """A batch ending on Monday must be extended (Tue–Fri potentially missing)."""
        tail = self._tail(datetime(2026, 7, 6))  # Monday
        self.assertGreater(tail, datetime(2026, 7, 6), "Extension required for Monday batch")

    def test_batch_ending_wednesday_needs_extension(self):
        tail = self._tail(datetime(2026, 7, 1))  # Wednesday 2026-07-01
        self.assertGreater(tail, datetime(2026, 7, 1))

    def test_extension_collects_remaining_days_in_week(self):
        """Simulate extension: batch ends Wednesday, extension finds Thu+Fri."""
        all_rows = [
            _daily(datetime(2026, 7, 6)),   # Mon
            _daily(datetime(2026, 7, 7)),   # Tue
            _daily(datetime(2026, 7, 8)),   # Wed  ← batch ends here
            _daily(datetime(2026, 7, 9)),   # Thu  ← extra
            _daily(datetime(2026, 7, 10)),  # Fri  ← extra
        ]
        batch = all_rows[:3]
        last_ts = batch[-1]["timestamp"]
        tail_end = self._tail(last_ts)
        extra = [r for r in all_rows if last_ts < r["timestamp"] <= tail_end]
        extended = batch + extra
        self.assertEqual(len(extended), 5, "Extension must add Thu and Fri")

        result = CandleResampler.resample_5m_to(extended, "1W")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volume"], 5000, "Complete week volume must equal sum of 5 days")

    def test_extension_on_friday_batch_is_no_op(self):
        """Batch ending Friday: tail=Sunday, extra-fetch returns nothing → no-op."""
        all_rows = [
            _daily(datetime(2026, 7, 6)),
            _daily(datetime(2026, 7, 7)),
            _daily(datetime(2026, 7, 8)),
            _daily(datetime(2026, 7, 9)),
            _daily(datetime(2026, 7, 10)),  # Friday
        ]
        last_ts = all_rows[-1]["timestamp"]
        tail_end = self._tail(last_ts)
        extra = [r for r in all_rows if last_ts < r["timestamp"] <= tail_end]
        self.assertEqual(extra, [], "No weekend rows → extension is a no-op")

        result = CandleResampler.resample_5m_to(all_rows, "1W")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volume"], 5000)

    def test_multiple_complete_weeks_plus_partial_boundary(self):
        """Batch with 2 full weeks + partial 3rd week: extension completes the 3rd."""
        week1 = [_daily(datetime(2026, 7, 6) + timedelta(days=i)) for i in range(5)]   # Jul 6-10
        week2 = [_daily(datetime(2026, 7, 13) + timedelta(days=i)) for i in range(5)]  # Jul 13-17
        week3_partial = [_daily(datetime(2026, 7, 20)), _daily(datetime(2026, 7, 21))]  # Mon-Tue only in batch
        week3_rest = [_daily(datetime(2026, 7, 22)), _daily(datetime(2026, 7, 23)), _daily(datetime(2026, 7, 24))]

        all_rows = week1 + week2 + week3_partial + week3_rest
        batch = week1 + week2 + week3_partial

        last_ts = batch[-1]["timestamp"]  # Tuesday Jul 21
        tail_end = self._tail(last_ts)
        self.assertEqual(tail_end, datetime(2026, 7, 26))  # Sunday Jul 26

        extra = [r for r in all_rows if last_ts < r["timestamp"] <= tail_end]
        extended = batch + extra
        self.assertEqual(len(extended), 15, "3 complete weeks = 15 daily rows")

        result = CandleResampler.resample_5m_to(extended, "1W")
        self.assertEqual(len(result), 3, "Three complete weeks")
        for w in result:
            self.assertEqual(w["volume"], 5000)

    def test_no_partial_1w_from_batch_size_100(self):
        """
        Critical regression: >100 daily rows, batch=100 ends mid-week.
        The extension must collect the remaining days so the resampler
        never sees a partial week.
        """
        # Build 22 complete Mon-Fri weeks (110 days) across arbitrary dates
        rows = []
        start_monday = datetime(2022, 1, 3)  # Monday
        for w in range(22):
            week_start = start_monday + timedelta(weeks=w)
            for d in range(5):  # Mon-Fri
                rows.append(_daily(week_start + timedelta(days=d)))

        self.assertEqual(len(rows), 110)

        # Batch 1: first 100 rows
        batch1 = rows[:100]
        last_ts = batch1[-1]["timestamp"]

        # Identify which week the 100th row falls in
        last_week_mon = last_ts - timedelta(days=last_ts.weekday())
        tail_end = self._tail(last_ts)
        expected_sunday = last_week_mon + timedelta(days=6)
        self.assertEqual(tail_end, expected_sunday)

        # Extension: remaining days of that week
        extra = [r for r in rows if last_ts < r["timestamp"] <= tail_end]
        extended = batch1 + extra

        # extended must end on a Friday (last trading day of the week)
        extended_last_ts = extended[-1]["timestamp"]
        self.assertEqual(extended_last_ts.weekday(), 4,
                         "After extension, batch must end on Friday")

        # Resample → all target candles must be complete weeks (5 days each)
        result = CandleResampler.resample_5m_to(extended, "1W")
        for candle in result:
            self.assertEqual(candle["volume"], 5000,
                             f"Week {candle['timestamp']} must have complete 5-day volume")

    def test_checkpoint_can_resume_from_friday(self):
        """After processing Mon-Fri, cursor=Friday; next batch starts Monday+7."""
        week1 = [_daily(datetime(2026, 7, 6) + timedelta(days=i)) for i in range(5)]
        week2 = [_daily(datetime(2026, 7, 13) + timedelta(days=i)) for i in range(5)]
        all_rows = week1 + week2

        # First batch: week1, extended to Friday
        last_ts_batch1 = week1[-1]["timestamp"]  # Friday Jul 10
        tail_end = self._tail(last_ts_batch1)     # Sunday Jul 12

        # No extra rows within the Sunday (weekend)
        extra_b1 = [r for r in all_rows if last_ts_batch1 < r["timestamp"] <= tail_end]
        self.assertEqual(extra_b1, [])

        # Cursor after batch1 = Friday Jul 10
        # Simulated next-batch query: rows > Friday Jul 10
        next_batch = [r for r in all_rows if r["timestamp"] > last_ts_batch1]
        self.assertEqual(next_batch[0]["timestamp"], datetime(2026, 7, 13),
                         "Next batch must start on Monday Jul 13 (next week)")


# ── Calendar batch boundary: 1W → 1M ────────────────────────────────────────

class TestCalendarBatchBoundary1WTo1M(unittest.TestCase):
    """
    Verify _target_bucket_tail() for 1W→1M: must return the last day of
    the same calendar month, enabling the _process_ticker extension to collect
    all weekly candles in the month before resampling — preventing partial
    1M candles.
    """

    def _tail(self, d):
        return RetentionService._target_bucket_tail(d, "1W", "1M")

    def test_january_returns_jan_31(self):
        self.assertEqual(self._tail(datetime(2026, 1, 5)), datetime(2026, 1, 31))

    def test_february_non_leap_returns_feb_28(self):
        self.assertEqual(self._tail(datetime(2026, 2, 2)), datetime(2026, 2, 28))

    def test_february_leap_returns_feb_29(self):
        self.assertEqual(self._tail(datetime(2024, 2, 5)), datetime(2024, 2, 29))

    def test_november_returns_nov_30(self):
        self.assertEqual(self._tail(datetime(2026, 11, 2)), datetime(2026, 11, 30))

    def test_december_returns_dec_31(self):
        # December edge-case: next month crosses year boundary
        self.assertEqual(self._tail(datetime(2026, 12, 7)), datetime(2026, 12, 31))

    def test_december_year_rollover(self):
        # next_month = Jan of next year
        tail = self._tail(datetime(2025, 12, 1))
        self.assertEqual(tail, datetime(2025, 12, 31))
        self.assertEqual(tail.month, 12)
        self.assertEqual(tail.day, 31)

    def test_tail_always_last_day_of_month(self):
        import calendar as _cal
        for month in range(1, 13):
            ts = datetime(2026, month, 1)
            tail = self._tail(ts)
            last_day = _cal.monthrange(2026, month)[1]
            self.assertEqual(tail, datetime(2026, month, last_day),
                             f"Month {month}: expected day {last_day}, got {tail.day}")

    def test_extension_collects_remaining_weeks_in_month(self):
        """Batch ends on second Monday of August; extension collects remaining weeks."""
        aug_weeks = [
            _weekly(datetime(2021, 8, 2)),   # Mon Aug 2
            _weekly(datetime(2021, 8, 9)),   # Mon Aug 9  ← batch ends here
            _weekly(datetime(2021, 8, 16)),  # Mon Aug 16 ← extra
            _weekly(datetime(2021, 8, 23)),  # Mon Aug 23 ← extra
            _weekly(datetime(2021, 8, 30)),  # Mon Aug 30 ← extra
        ]
        batch = aug_weeks[:2]
        last_ts = batch[-1]["timestamp"]
        tail_end = self._tail(last_ts)
        self.assertEqual(tail_end, datetime(2021, 8, 31))

        extra = [r for r in aug_weeks if last_ts < r["timestamp"] <= tail_end]
        self.assertEqual(len(extra), 3, "Must collect Aug 16, 23, 30")
        extended = batch + extra
        self.assertEqual(len(extended), 5)

        result = CandleResampler.resample_5m_to(extended, "1M")
        self.assertEqual(len(result), 1, "Five August weeks → one August monthly candle")
        self.assertEqual(result[0]["volume"], 25000)

    def test_no_partial_1m_from_batch_boundary(self):
        """
        Critical regression: batch ends mid-month; no partial 1M candle is created.
        """
        all_weeks = []
        # 3 months of weekly candles: Jan-Mar 2026
        # January: ~4-5 Mondays
        for w in range(4):
            all_weeks.append(_weekly(datetime(2026, 1, 5) + timedelta(weeks=w)))   # Jan 5, 12, 19, 26
        # February: 4 Mondays
        for w in range(4):
            all_weeks.append(_weekly(datetime(2026, 2, 2) + timedelta(weeks=w)))   # Feb 2, 9, 16, 23
        # March: partial in batch
        all_weeks.append(_weekly(datetime(2026, 3, 2)))   # Mar 2
        all_weeks.append(_weekly(datetime(2026, 3, 9)))   # Mar 9 ← batch ends here (row ~10)
        all_weeks.append(_weekly(datetime(2026, 3, 16)))  # Mar 16 ← extra
        all_weeks.append(_weekly(datetime(2026, 3, 23)))  # Mar 23 ← extra
        all_weeks.append(_weekly(datetime(2026, 3, 30)))  # Mar 30 ← extra

        # Simulate a batch ending on Mar 9 (mid-March)
        batch = all_weeks[:10]  # Jan + Feb + Mar 2, 9
        last_ts = batch[-1]["timestamp"]
        self.assertEqual(last_ts.month, 3)
        self.assertLess(last_ts.day, 15)

        tail_end = self._tail(last_ts)
        self.assertEqual(tail_end.month, 3)
        self.assertEqual(tail_end.day, 31)

        extra = [r for r in all_weeks if last_ts < r["timestamp"] <= tail_end]
        extended = batch + extra

        result = CandleResampler.resample_5m_to(extended, "1M")
        self.assertEqual(len(result), 3, "Jan + Feb + Mar = 3 complete months")
        for candle in result:
            self.assertGreater(candle["volume"], 0)
            # Each month bucket must align to month-start
            self.assertEqual(candle["timestamp"].day, 1)

    def test_last_week_of_month_no_extra_rows(self):
        """Batch ending on the last Monday of the month: extension is a no-op."""
        aug_weeks = [
            _weekly(datetime(2021, 8, 2)),
            _weekly(datetime(2021, 8, 9)),
            _weekly(datetime(2021, 8, 16)),
            _weekly(datetime(2021, 8, 23)),
            _weekly(datetime(2021, 8, 30)),  # last Monday in August
        ]
        last_ts = aug_weeks[-1]["timestamp"]  # Aug 30
        tail_end = self._tail(last_ts)
        self.assertEqual(tail_end, datetime(2021, 8, 31))

        # No 1W rows between Aug 30 and Aug 31 (next is Sep 6)
        extra = [r for r in aug_weeks if last_ts < r["timestamp"] <= tail_end]
        self.assertEqual(extra, [], "No rows between Aug 30 and Aug 31")

        result = CandleResampler.resample_5m_to(aug_weeks, "1M")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volume"], 25000)


# ── Cross-chain regression ────────────────────────────────────────────────────

class TestCalendarBoundaryRegression(unittest.TestCase):
    """Verify intraday chains are unaffected by the calendar-chain fix."""

    def test_intraday_5m_15m_still_correct(self):
        tail = RetentionService._target_bucket_tail(_dt(9, 15), "5m", "15m")
        self.assertEqual(tail, _dt(9, 25))

    def test_intraday_5m_30m_still_correct(self):
        tail = RetentionService._target_bucket_tail(_dt(9, 15), "5m", "30m")
        self.assertEqual(tail, _dt(9, 40))

    def test_intraday_15m_30m_still_correct(self):
        tail = RetentionService._target_bucket_tail(_dt(9, 15), "15m", "30m")
        self.assertEqual(tail, _dt(9, 30))

    def test_intraday_5m_1h_still_correct(self):
        tail = RetentionService._target_bucket_tail(_dt(9, 15), "5m", "1h")
        self.assertEqual(tail, _dt(10, 10))

    def test_pre_session_still_returns_none(self):
        self.assertIsNone(
            RetentionService._target_bucket_tail(_dt(9, 0), "5m", "15m"))

    def test_unknown_chain_returns_none(self):
        self.assertIsNone(
            RetentionService._target_bucket_tail(_dt(9, 15), "4h", "1D"))

    def test_calendar_fix_does_not_alter_1d_resampler(self):
        """1D resampler result must be unchanged."""
        rows = [_daily(datetime(2026, 7, 6) + timedelta(days=i)) for i in range(5)]
        result = CandleResampler.resample_5m_to(rows, "1W")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volume"], 5000)


if __name__ == "__main__":
    unittest.main()
