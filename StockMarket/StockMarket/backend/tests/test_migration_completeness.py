"""
Tests for migration/completeness.py -- the trading-session-aware gap
detector added because migration/validator.py's validate_source() never
checked whether every trading day in a requested range actually has a
candle. Uses a real in-memory SQLite DB + the real Candle model, and drives
exchange_calendar.nse_calendar directly (with a small, explicit weekday-only
holiday set) rather than mocking it, so the actual trading-day arithmetic
is exercised.
"""
import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Candle
from migration.completeness import check_ticker_completeness, missing_date_ranges
from exchange_calendar import nse_calendar


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Candle.__table__.create(bind=engine)
    return sessionmaker(bind=engine)()


def _add(db, ticker, d: date, source="ANGELONE"):
    db.add(Candle(
        ticker=ticker, timeframe="1D", timestamp=datetime(d.year, d.month, d.day),
        open=1, high=1, low=1, close=1, volume=1,
        is_completed=True, data_source=source, is_backfilled=(source == "ANGELONE"),
    ))


class TestCompletenessBase(unittest.TestCase):
    def setUp(self):
        # Deterministic: no holidays loaded, so is_trading_day == weekday check.
        nse_calendar.load_holidays(set())


class TestHolidayAwareness(unittest.TestCase):
    """Proves the detector consults exchange_calendar.nse_calendar's loaded
    holiday set (not just weekday arithmetic) -- a real NSE holiday with a
    loaded holiday date must NOT be counted as a missing session, exactly
    the same as a weekend. This is what the empty-holidays-table caveat in
    completeness.py's docstring depends on: the code is holiday-aware
    whenever holiday data IS loaded, it just has none loaded in production
    today."""
    def test_loaded_holiday_not_counted_as_missing(self):
        # 2024-01-26 (Fri) is Republic Day -- a real NSE holiday. Load it
        # explicitly so this test does not depend on production DB state.
        nse_calendar.load_holidays({date(2024, 1, 26)})
        db = _make_session()
        _add(db, "TESTCO", date(2024, 1, 25))  # Thursday, present
        # 2024-01-26 (Friday, holiday) deliberately absent
        _add(db, "TESTCO", date(2024, 1, 29))  # Monday, present
        db.commit()

        result = check_ticker_completeness(db, "TESTCO", "1D", date(2024, 1, 25), date(2024, 1, 29))
        self.assertTrue(result.passed, f"a loaded holiday must not be flagged missing: {result.summary()}")
        self.assertEqual(result.expected_sessions, 2, "the holiday itself must not count as an expected session")

        nse_calendar.load_holidays(set())  # reset for other tests

    def test_missing_non_holiday_weekday_still_detected_with_holidays_loaded(self):
        """Loading holidays must not accidentally suppress detection of a
        real gap on an ordinary trading day."""
        nse_calendar.load_holidays({date(2024, 1, 26)})
        db = _make_session()
        _add(db, "TESTCO", date(2024, 1, 25))
        # 2024-01-26 holiday (correctly absent)
        # 2024-01-29 (Monday) missing for real -- must still be caught
        _add(db, "TESTCO", date(2024, 1, 30))
        db.commit()

        result = check_ticker_completeness(db, "TESTCO", "1D", date(2024, 1, 25), date(2024, 1, 30))
        self.assertFalse(result.passed)
        self.assertEqual(result.missing_sessions, 1)
        self.assertEqual(result.gaps[0].start, date(2024, 1, 29))

        nse_calendar.load_holidays(set())  # reset for other tests


class TestFullyComplete(TestCompletenessBase):
    def test_all_weekdays_present_passes(self):
        db = _make_session()
        # Mon 2024-01-01 .. Fri 2024-01-05, all 5 trading days present.
        for day in range(1, 6):
            _add(db, "TESTCO", date(2024, 1, day))
        db.commit()

        result = check_ticker_completeness(db, "TESTCO", "1D", date(2024, 1, 1), date(2024, 1, 5))
        self.assertTrue(result.passed)
        self.assertEqual(result.missing_sessions, 0)
        self.assertEqual(result.expected_sessions, 5)
        self.assertEqual(result.gaps, [])

    def test_weekends_not_required(self):
        """2024-01-06/07 are Sat/Sun -- must not be counted as missing."""
        db = _make_session()
        _add(db, "TESTCO", date(2024, 1, 5))   # Friday
        _add(db, "TESTCO", date(2024, 1, 8))   # Monday
        db.commit()

        result = check_ticker_completeness(db, "TESTCO", "1D", date(2024, 1, 5), date(2024, 1, 8))
        self.assertTrue(result.passed)
        self.assertEqual(result.expected_sessions, 2)


class TestInternalGap(TestCompletenessBase):
    def test_detects_gap_in_middle_of_range(self):
        """2024-08-12 -> 2024-11-08 present, then a real gap, matching the
        exact pattern found in the second controlled batch."""
        db = _make_session()
        _add(db, "TESTCO", date(2024, 8, 12))
        _add(db, "TESTCO", date(2024, 8, 13))
        # gap: 2024-08-14 (Wed) missing
        _add(db, "TESTCO", date(2024, 8, 15))
        db.commit()

        result = check_ticker_completeness(db, "TESTCO", "1D", date(2024, 8, 12), date(2024, 8, 15))
        self.assertFalse(result.passed)
        self.assertEqual(result.missing_sessions, 1)
        self.assertEqual(len(result.gaps), 1)
        self.assertEqual(result.gaps[0].start, date(2024, 8, 14))
        self.assertEqual(result.gaps[0].end, date(2024, 8, 14))


class TestBoundaryGap(TestCompletenessBase):
    def test_detects_gap_between_two_data_sources_generically(self):
        """The exact real-world case: ANGELONE ends 2026-05-08, YFINANCE
        starts 2026-07-21 -- a real multi-week gap at the source boundary.
        The detector must catch this without any special-cased boundary
        logic, purely from actual-vs-expected trading days."""
        db = _make_session()
        _add(db, "FACT", date(2026, 5, 7), source="ANGELONE")
        _add(db, "FACT", date(2026, 5, 8), source="ANGELONE")
        _add(db, "FACT", date(2026, 7, 21), source="YFINANCE")
        _add(db, "FACT", date(2026, 7, 22), source="YFINANCE")
        db.commit()

        result = check_ticker_completeness(db, "FACT", "1D", date(2026, 5, 7), date(2026, 7, 22))
        self.assertFalse(result.passed)
        self.assertGreater(result.missing_sessions, 30, "a ~2.5 month gap must show as dozens of missing sessions")
        ranges = missing_date_ranges(result)
        self.assertEqual(len(ranges), 1)
        # 2026-05-08 is a Friday -- the next expected trading day is Monday
        # 2026-05-11, not the Saturday immediately after.
        self.assertEqual(ranges[0][0], date(2026, 5, 11))
        self.assertEqual(ranges[0][1], date(2026, 7, 20))

    def test_contiguous_boundary_between_sources_is_not_a_gap(self):
        """The clean case (e.g. TCS, AAVAS in the real run): ANGELONE ends
        the day before YFINANCE begins -- must pass with zero gaps."""
        db = _make_session()
        _add(db, "TCS", date(2026, 7, 20), source="ANGELONE")  # Monday
        _add(db, "TCS", date(2026, 7, 21), source="YFINANCE")  # Tuesday
        db.commit()

        result = check_ticker_completeness(db, "TCS", "1D", date(2026, 7, 20), date(2026, 7, 21))
        self.assertTrue(result.passed)


class TestMultipleGaps(TestCompletenessBase):
    def test_multiple_separate_gaps_all_reported(self):
        db = _make_session()
        _add(db, "TESTCO", date(2024, 1, 1))   # Mon
        # gap: 1/2, 1/3 missing
        _add(db, "TESTCO", date(2024, 1, 4))   # Thu
        _add(db, "TESTCO", date(2024, 1, 5))   # Fri
        # weekend skipped automatically
        # gap: 1/8 missing
        _add(db, "TESTCO", date(2024, 1, 9))   # Tue
        db.commit()

        result = check_ticker_completeness(db, "TESTCO", "1D", date(2024, 1, 1), date(2024, 1, 9))
        self.assertEqual(len(result.gaps), 2)
        self.assertEqual(result.missing_sessions, 3)


if __name__ == "__main__":
    unittest.main()
