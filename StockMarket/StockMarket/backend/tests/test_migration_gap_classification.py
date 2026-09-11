"""
Tests for evidence-based gap classification (migration/completeness.py).

The production `holidays` table is empty, so nse_calendar only excludes
weekends and every real NSE holiday looks like a missing session. Rather
than invent holiday data, gaps are classified from what the database
already proves: a day on which NO ticker anywhere has a candle is a
market-wide closure; a day on which the rest of the market traded but this
ticker did not is a REAL, still-unexplained gap.

The critical property under test is that classification cannot be used to
hide a genuine gap -- hiding would require the entire universe to vanish on
that date, which is precisely what a closure is.
"""
import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Candle
from migration.completeness import (
    GapCause, check_ticker_completeness, classify_result, daily_market_coverage,
)
from exchange_calendar import nse_calendar


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Candle.__table__.create(bind=engine)
    return sessionmaker(bind=engine)()


def _add(db, ticker, d: date):
    db.add(Candle(
        ticker=ticker, timeframe="1D", timestamp=datetime(d.year, d.month, d.day),
        open=1, high=1, low=1, close=1, volume=1,
        is_completed=True, data_source="ANGELONE", is_backfilled=True,
    ))


def _populate_market(db, days, n_tickers=50, skip=()):
    """Give a broad universe data on every day in `days` except `skip`."""
    for i in range(n_tickers):
        for d in days:
            if d in skip:
                continue
            _add(db, f"MKT{i:03d}", d)


class TestGapClassification(unittest.TestCase):
    def setUp(self):
        nse_calendar.load_holidays(set())
        # Mon..Fri across two weeks (all weekdays -> all "trading days")
        self.days = [date(2024, 1, d) for d in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12)]

    def test_market_wide_closure_classified_as_closure_not_real_gap(self):
        """2024-01-04 absent for EVERYONE -> exchange was shut. Must be
        MARKET_CLOSURE and must not count as unexplained."""
        db = _make_session()
        holiday = date(2024, 1, 4)
        _populate_market(db, self.days, skip=(holiday,))
        for d in self.days:
            if d != holiday:
                _add(db, "TARGET", d)
        db.commit()

        r = check_ticker_completeness(db, "TARGET", "1D", self.days[0], self.days[-1])
        self.assertEqual(r.missing_sessions, 1, "raw measurement must still report it")

        c = classify_result(db, r)
        self.assertEqual(c["market_closure"], 1)
        self.assertEqual(c["security_absent"], 0)
        self.assertEqual(c["unexplained"], 0, "a market-wide closure is explained, not unexplained")

    def test_security_specific_absence_stays_a_real_gap(self):
        """The whole market traded on 2024-01-09 but TARGET has no candle.
        This is exactly what must NOT be explained away."""
        db = _make_session()
        gap_day = date(2024, 1, 9)
        _populate_market(db, self.days)
        for d in self.days:
            if d != gap_day:
                _add(db, "TARGET", d)
        db.commit()

        r = check_ticker_completeness(db, "TARGET", "1D", self.days[0], self.days[-1])
        c = classify_result(db, r)
        self.assertEqual(c["security_absent"], 1)
        self.assertEqual(c["market_closure"], 0)
        self.assertEqual(c["unexplained"], 1, "a real gap must remain unexplained")
        cause, coverage = c["detail"][gap_day]
        self.assertEqual(cause, GapCause.SECURITY_ABSENT)
        self.assertGreater(coverage, 0, "other tickers demonstrably had data that day")

    def test_closure_and_real_gap_reported_separately_in_one_ticker(self):
        db = _make_session()
        holiday, gap_day = date(2024, 1, 4), date(2024, 1, 9)
        _populate_market(db, self.days, skip=(holiday,))
        for d in self.days:
            if d not in (holiday, gap_day):
                _add(db, "TARGET", d)
        db.commit()

        r = check_ticker_completeness(db, "TARGET", "1D", self.days[0], self.days[-1])
        c = classify_result(db, r)
        self.assertEqual(r.missing_sessions, 2)
        self.assertEqual(c["market_closure"], 1)
        self.assertEqual(c["security_absent"], 1)
        self.assertEqual(c["unexplained"], 1)

    def test_sparse_universe_is_indeterminate_not_absolved(self):
        """If only a couple of tickers have data that day, the database is
        too thin to prove anything -- it must be INDETERMINATE and still
        counted as unexplained, never silently treated as a holiday."""
        db = _make_session()
        sparse_day = date(2024, 1, 9)
        _populate_market(db, self.days, skip=(sparse_day,))
        # exactly two stragglers hold that day
        _add(db, "MKT000", sparse_day)
        _add(db, "MKT001", sparse_day)
        for d in self.days:
            if d != sparse_day:
                _add(db, "TARGET", d)
        db.commit()

        r = check_ticker_completeness(db, "TARGET", "1D", self.days[0], self.days[-1])
        c = classify_result(db, r)
        self.assertEqual(c["indeterminate"], 1)
        self.assertEqual(c["market_closure"], 0, "2 of 50 tickers is not proof of a closure")
        self.assertEqual(c["unexplained"], 1, "indeterminate must NOT be absolved")

    def test_raw_missing_count_never_rewritten_by_classification(self):
        """Classification adds interpretation; it must never alter the
        underlying measurement."""
        db = _make_session()
        holiday = date(2024, 1, 4)
        _populate_market(db, self.days, skip=(holiday,))
        for d in self.days:
            if d != holiday:
                _add(db, "TARGET", d)
        db.commit()

        r = check_ticker_completeness(db, "TARGET", "1D", self.days[0], self.days[-1])
        before = r.missing_sessions
        classify_result(db, r)
        self.assertEqual(r.missing_sessions, before)
        self.assertFalse(r.passed, "the ticker is still not 'complete' just because a day was a holiday")


class TestDailyMarketCoverage(unittest.TestCase):
    def test_coverage_counts_distinct_tickers_per_day(self):
        db = _make_session()
        _add(db, "A", date(2024, 1, 2))
        _add(db, "B", date(2024, 1, 2))
        _add(db, "A", date(2024, 1, 3))
        db.commit()
        cov = daily_market_coverage(db, date(2024, 1, 1), date(2024, 1, 5))
        self.assertEqual(cov.get(date(2024, 1, 2)), 2)
        self.assertEqual(cov.get(date(2024, 1, 3)), 1)
        self.assertIsNone(cov.get(date(2024, 1, 4)))


if __name__ == "__main__":
    unittest.main()
