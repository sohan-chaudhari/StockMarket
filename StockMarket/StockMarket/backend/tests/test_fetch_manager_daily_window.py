"""
Regression tests for the chunk-first-day data-loss bug found by the Phase 3
repair audit.

AngelOneFetchManager.fetch() floored from_date at 09:00 for non-intraday
intervals. Angel One's daily candles are stamped at MIDNIGHT (00:00), so the
requested first day's own candle fell before the window and the API omitted
it -- the first calendar day of EVERY chunk was silently lost.

It stayed hidden because chunk_date_range() advances by max_days + 1 = 91
days = exactly 13 weeks, so every chunk start lands on the same weekday as
the range start. The original 1D run started on a Sunday (all drops were
non-trading days); the repair run started on a Monday, and all eight drops
were real trading sessions.

Mocked at the historical_service boundary -- no network calls.
"""
import unittest
from datetime import date, datetime

from unittest.mock import patch

from migration.config import MigrationConfig, chunk_date_range
from migration.fetch_manager import AngelOneFetchManager


def _capture_window(angel_interval, from_date, to_date):
    """Runs a fetch and returns the (from_date, to_date) datetimes actually
    handed to historical_service."""
    mgr = AngelOneFetchManager(MigrationConfig(requests_per_second=1000, retry_max=1))
    with patch("migration.fetch_manager.historical_service") as mock_hs:
        mock_hs.is_logged_in = True
        mock_hs.get_historical_candles.return_value = []
        mgr.fetch("RELIANCE", angel_interval, from_date, to_date)
    kwargs = mock_hs.get_historical_candles.call_args.kwargs
    return kwargs["from_date"], kwargs["to_date"]


class TestDailyWindowIncludesFirstDay(unittest.TestCase):
    def test_one_day_from_date_floored_to_midnight_not_0900(self):
        """The core fix: a ONE_DAY request must start at 00:00 so the
        midnight-stamped candle for that very date is inside the window."""
        frm, _ = _capture_window("ONE_DAY", date(2024, 11, 11), date(2025, 2, 9))
        self.assertEqual((frm.hour, frm.minute), (0, 0),
                         "a 09:00 floor excludes the first day's own 00:00 candle")
        self.assertEqual(frm.date(), date(2024, 11, 11))

    def test_midnight_floor_covers_a_midnight_stamped_candle(self):
        """Directly asserts the property that actually mattered: the first
        day's candle timestamp is >= the requested window start."""
        frm, _ = _capture_window("ONE_DAY", date(2024, 11, 11), date(2025, 2, 9))
        first_day_candle_ts = datetime(2024, 11, 11, 0, 0)
        self.assertGreaterEqual(first_day_candle_ts, frm,
                                "the requested day's own candle must fall inside the window")

    def test_to_date_still_covers_last_day(self):
        _, to = _capture_window("ONE_DAY", date(2024, 11, 11), date(2025, 2, 9))
        self.assertEqual(to.date(), date(2025, 2, 9))
        self.assertGreaterEqual(datetime(2025, 2, 9, 0, 0), datetime.combine(date(2025, 2, 9), datetime.min.time()))
        self.assertLessEqual(datetime(2025, 2, 9, 0, 0), to)


class TestIntradayWindowUnchanged(unittest.TestCase):
    """Intraday candles ARE stamped from the 09:15 session open, so their
    floor must stay 09:15 -- the fix must not disturb the working
    5m/15m/30m/1h chain."""

    def test_intraday_intervals_still_floor_at_0915(self):
        for interval in ("FIVE_MINUTE", "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR"):
            with self.subTest(interval=interval):
                frm, _ = _capture_window(interval, date(2026, 1, 5), date(2026, 1, 9))
                self.assertEqual((frm.hour, frm.minute), (9, 15))


class TestChunkBoundaryWeekdayAliasing(unittest.TestCase):
    """Documents WHY the bug hid for so long, so nobody 'simplifies' the
    midnight floor away later: consecutive chunk starts are 91 days apart,
    which is exactly 13 weeks, so they all share one weekday."""

    def test_chunk_starts_are_91_days_apart_and_share_a_weekday(self):
        chunks = chunk_date_range(date(2024, 8, 12), date(2026, 7, 20), 90)
        starts = [c[0] for c in chunks]
        self.assertGreater(len(starts), 2)
        for earlier, later in zip(starts, starts[1:]):
            self.assertEqual((later - earlier).days, 91)
        self.assertEqual(len({s.weekday() for s in starts}), 1,
                         "every chunk start shares one weekday -- so a first-day drop "
                         "either costs a real session every time, or never")

    def test_monday_start_reproduces_the_eight_observed_dates(self):
        """The exact dates 13 tickers were each missing after the repair."""
        starts = [c[0] for c in chunk_date_range(date(2024, 8, 12), date(2026, 7, 20), 90)]
        observed = [date(2024, 8, 12), date(2024, 11, 11), date(2025, 2, 10),
                    date(2025, 5, 12), date(2025, 8, 11), date(2025, 11, 10),
                    date(2026, 2, 9), date(2026, 5, 11)]
        for d in observed:
            self.assertIn(d, starts, f"{d} should be a chunk start (and was dropped pre-fix)")


if __name__ == "__main__":
    unittest.main()
