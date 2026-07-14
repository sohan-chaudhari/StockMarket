import unittest
from datetime import datetime, date, time, timedelta
from exchange_calendar import NSECalendar, SessionInfo, IST, NSE_OPEN_TIME, NSE_CLOSE_TIME


class TestNSECalendar(unittest.TestCase):
    def setUp(self):
        self.cal = NSECalendar()
        self.cal.load_holidays({date(2026, 7, 17)})  # example holiday

    def test_is_trading_day_normal(self):
        wed = date(2026, 7, 1)
        if wed.weekday() < 5:
            self.assertTrue(self.cal.is_trading_day(wed))

    def test_is_trading_day_weekend(self):
        sat = date(2026, 7, 4)
        self.assertFalse(self.cal.is_trading_day(sat))

    def test_is_trading_day_holiday(self):
        holiday = date(2026, 7, 17)
        self.assertFalse(self.cal.is_trading_day(holiday))

    def test_is_market_open_during_hours(self):
        dt = datetime(2026, 7, 1, 10, 0, 0, tzinfo=IST)
        self.assertTrue(self.cal.is_market_open(dt))

    def test_is_market_open_pre_market(self):
        dt = datetime(2026, 7, 1, 8, 0, 0, tzinfo=IST)
        self.assertFalse(self.cal.is_market_open(dt))

    def test_is_market_open_post_market(self):
        dt = datetime(2026, 7, 1, 16, 0, 0, tzinfo=IST)
        self.assertFalse(self.cal.is_market_open(dt))

    def test_is_market_open_holiday(self):
        dt = datetime(2026, 7, 17, 10, 0, 0, tzinfo=IST)
        self.assertFalse(self.cal.is_market_open(dt))

    def test_current_session_trading_day(self):
        d = datetime.now(IST).date()
        if d.weekday() < 5:
            session = self.cal.current_session()
            if session:
                self.assertEqual(session.open_time, NSE_OPEN_TIME)
                self.assertEqual(session.close_time, NSE_CLOSE_TIME)

    def test_current_session_non_trading_day(self):
        # Force a non-trading day
        sat = date(2026, 7, 4)
        with unittest.mock.patch("exchange_calendar.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(sat.year, sat.month, sat.day, 10, 0, tzinfo=IST)
            session = self.cal.current_session()
            self.assertIsNone(session)

    def test_session_count_in_range(self):
        start = date(2026, 7, 1)
        end = date(2026, 7, 7)
        count = self.cal.session_count_in_range(start, end)
        self.assertGreaterEqual(count, 3)  # at least M-W (excluding holiday on 17th)
        self.assertLessEqual(count, 5)

    def test_add_special_session(self):
        special = SessionInfo(date=date(2026, 7, 1), open_time=time(10, 0), close_time=time(14, 0), session_type="SPECIAL")
        self.cal.add_special_session(special)
        self.assertTrue(self.cal.is_shortened_session(date(2026, 7, 1)))

    def test_session_boundary_intraday(self):
        dt = datetime(2026, 7, 1, 10, 7, 0, tzinfo=IST)
        boundary = self.cal.session_boundary(dt, {"type": "intraday", "minutes": 15})
        self.assertEqual(boundary.hour, 10)
        self.assertEqual(boundary.minute, 0)

    def test_session_boundary_intraday_first_bucket(self):
        dt = datetime(2026, 7, 1, 9, 17, 0, tzinfo=IST)
        boundary = self.cal.session_boundary(dt, {"type": "intraday", "minutes": 5})
        self.assertEqual(boundary.hour, 9)
        self.assertEqual(boundary.minute, 15)

    def test_session_boundary_intraday_post_market(self):
        dt = datetime(2026, 7, 1, 16, 0, 0, tzinfo=IST)
        boundary = self.cal.session_boundary(dt, {"type": "intraday", "minutes": 15})
        # Should snap to last 15m bucket of the session
        self.assertEqual(boundary.hour, 15)
        self.assertTrue(boundary.minute in (15, 30))  # last 15m bucket

    def test_session_boundary_session_type(self):
        dt = datetime(2026, 7, 1, 10, 30, 0, tzinfo=IST)
        boundary = self.cal.session_boundary(dt, {"type": "session"})
        self.assertEqual(boundary.hour, 9)
        self.assertEqual(boundary.minute, 15)

    def test_session_boundary_week_type(self):
        dt = datetime(2026, 7, 1, 10, 0, 0, tzinfo=IST)
        boundary = self.cal.session_boundary(dt, {"type": "week"})
        self.assertEqual(boundary.weekday(), 0)  # Monday

    def test_session_boundary_month_type(self):
        dt = datetime(2026, 7, 15, 10, 0, 0, tzinfo=IST)
        boundary = self.cal.session_boundary(dt, {"type": "month"})
        self.assertEqual(boundary.day, 1)

    def test_load_holidays_replaces_set(self):
        new_holidays = {date(2026, 12, 25), date(2026, 1, 26)}
        self.cal.load_holidays(new_holidays)
        self.assertTrue(self.cal.is_trading_day(date(2026, 7, 17)))  # no longer a holiday
        self.assertFalse(self.cal.is_trading_day(date(2026, 12, 25)))

    def test_pre_market_boundary_session_start(self):
        dt = datetime(2026, 7, 1, 3, 0, 0, tzinfo=IST)
        boundary = self.cal._snap_intraday(dt, 5)
        self.assertEqual(boundary.hour, 9)
        self.assertEqual(boundary.minute, 15)


if __name__ == "__main__":
    unittest.main()
