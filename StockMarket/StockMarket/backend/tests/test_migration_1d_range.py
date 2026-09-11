import unittest
from datetime import date, timedelta
from migration.config import compute_1d_required_range, chunk_date_range


class TestCompute1DRequiredRange(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 8, 11)
        self.required_start = self.today - timedelta(days=730)  # 2024-08-12

    def test_no_existing_data_fetches_full_730_day_range(self):
        result = compute_1d_required_range(self.required_start, self.today, existing_start=None)
        self.assertEqual(result, (self.required_start, self.today))

    def test_partial_existing_data_narrows_the_range(self):
        """The exact audited scenario: candles already covers 2026-07-21 onward
        -- must fetch only required_start..2026-07-20, not re-request the
        21 days that already exist."""
        existing_start = date(2026, 7, 21)
        result = compute_1d_required_range(self.required_start, self.today, existing_start=existing_start)
        self.assertEqual(result, (self.required_start, date(2026, 7, 20)))

    def test_already_complete_ticker_needs_no_fetch(self):
        """existing_start on or before required_start -> the 730-day window
        is already satisfied. Must return None, not an empty/zero-width range
        that would still trigger a wasted API call."""
        existing_start = self.required_start  # exactly at the boundary
        result = compute_1d_required_range(self.required_start, self.today, existing_start=existing_start)
        self.assertIsNone(result)

    def test_existing_data_older_than_required_start_needs_no_fetch(self):
        existing_start = self.required_start - timedelta(days=100)
        result = compute_1d_required_range(self.required_start, self.today, existing_start=existing_start)
        self.assertIsNone(result)

    def test_one_day_gap(self):
        existing_start = self.required_start + timedelta(days=1)
        result = compute_1d_required_range(self.required_start, self.today, existing_start=existing_start)
        self.assertEqual(result, (self.required_start, self.required_start))

    def test_recently_listed_ticker_still_requests_full_range(self):
        """'Ticker with less than 730 days of actual listing history': this
        function has no way to know a listing date, and must not guess one --
        it always requests up to required_start. Angel One's own response for
        the pre-listing portion is simply empty; that's validated downstream,
        not pre-filtered here."""
        result = compute_1d_required_range(self.required_start, self.today, existing_start=None)
        self.assertEqual(result[0], self.required_start)

    def test_weekend_boundary_dates_are_not_adjusted(self):
        """1D retention is calendar-day based (Phase 3) -- a required_start or
        existing_start landing on a weekend must be used as-is, never shifted
        to the nearest weekday. 2024-08-11 (required_start's neighborhood) --
        confirm no silent adjustment happens for a Saturday boundary."""
        saturday_required_start = date(2026, 1, 3)  # a Saturday
        self.assertEqual(saturday_required_start.weekday(), 5)
        result = compute_1d_required_range(saturday_required_start, date(2026, 1, 10), existing_start=None)
        self.assertEqual(result[0], saturday_required_start)  # untouched, still Saturday

    def test_result_is_deterministic_across_repeated_calls(self):
        """Idempotency: same inputs must always produce the same proposed
        range -- calling the planner twice must not produce different work."""
        existing_start = date(2026, 7, 21)
        first = compute_1d_required_range(self.required_start, self.today, existing_start)
        second = compute_1d_required_range(self.required_start, self.today, existing_start)
        self.assertEqual(first, second)


class TestChunkDateRange(unittest.TestCase):
    def test_range_within_limit_is_a_single_chunk(self):
        start, end = date(2026, 1, 1), date(2026, 1, 30)
        chunks = chunk_date_range(start, end, max_days=90)
        self.assertEqual(chunks, [(start, end)])

    def test_range_exactly_at_the_90_day_limit_is_a_single_chunk(self):
        start = date(2026, 1, 1)
        end = start + timedelta(days=90)
        chunks = chunk_date_range(start, end, max_days=90)
        self.assertEqual(chunks, [(start, end)])

    def test_range_one_day_over_the_limit_splits_into_two_chunks(self):
        start = date(2026, 1, 1)
        end = start + timedelta(days=91)
        chunks = chunk_date_range(start, end, max_days=90)
        self.assertEqual(len(chunks), 2)

    def test_chunks_are_contiguous_and_cover_the_full_range_exactly(self):
        start, end = date(2024, 8, 12), date(2026, 8, 11)  # the real 730-day audit range
        chunks = chunk_date_range(start, end, max_days=90)
        self.assertEqual(chunks[0][0], start)
        self.assertEqual(chunks[-1][1], end)
        for i in range(len(chunks) - 1):
            this_end = chunks[i][1]
            next_start = chunks[i + 1][0]
            self.assertEqual(next_start, this_end + timedelta(days=1), "chunks must not overlap or leave a gap")

    def test_no_chunk_exceeds_max_days(self):
        start, end = date(2024, 8, 12), date(2026, 8, 11)
        chunks = chunk_date_range(start, end, max_days=90)
        for c_start, c_end in chunks:
            self.assertLessEqual((c_end - c_start).days, 90)

    def test_730_day_range_produces_expected_chunk_count(self):
        start = date(2024, 8, 12)
        end = date(2026, 8, 11)  # 730 days later
        chunks = chunk_date_range(start, end, max_days=90)
        # 730 days / 90-day chunks -> ceil(730/90) = 9 chunks
        self.assertEqual(len(chunks), 9)


if __name__ == "__main__":
    unittest.main()
