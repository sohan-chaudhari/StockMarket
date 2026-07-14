from abc import ABC, abstractmethod
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Set, Dict
from dataclasses import dataclass

IST = timezone(timedelta(hours=5, minutes=30))

NSE_OPEN_TIME = time(9, 15)
NSE_CLOSE_TIME = time(15, 30)
NSE_CLOSE_GRACE = time(15, 45)

_NSE_OPEN_SEC = NSE_OPEN_TIME.hour * 3600 + NSE_OPEN_TIME.minute * 60
_NSE_CLOSE_SEC = NSE_CLOSE_TIME.hour * 3600 + NSE_CLOSE_TIME.minute * 60


@dataclass
class SessionInfo:
    date: date
    open_time: time
    close_time: time
    session_type: str = "NORMAL"


class ExchangeCalendarProvider(ABC):
    @abstractmethod
    def is_market_open(self, dt: datetime) -> bool: ...

    @abstractmethod
    def is_trading_day(self, d: date) -> bool: ...

    @abstractmethod
    def current_session(self) -> Optional[SessionInfo]: ...

    @abstractmethod
    def session_count_in_range(self, start: date, end: date) -> int: ...

    @abstractmethod
    def session_boundary(self, dt: datetime, tf_config: dict) -> datetime: ...

    @abstractmethod
    def is_shortened_session(self, d: date) -> bool: ...


class NSECalendar(ExchangeCalendarProvider):
    def __init__(self):
        self._holidays: Set[date] = set()
        self._special_sessions: Dict[date, SessionInfo] = {}

    def load_holidays(self, holidays: Set[date]):
        self._holidays = holidays

    def add_special_session(self, session: SessionInfo):
        self._special_sessions[session.date] = session

    def is_market_open(self, dt: datetime) -> bool:
        if not self.is_trading_day(dt.date()):
            return False
        t = dt.time()
        return _NSE_OPEN_SEC <= (t.hour * 3600 + t.minute * 60) < _NSE_CLOSE_SEC

    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        if d in self._holidays:
            return False
        return True

    def current_session(self) -> Optional[SessionInfo]:
        today = datetime.now(IST).date()
        if not self.is_trading_day(today):
            return None
        if today in self._special_sessions:
            return self._special_sessions[today]
        return SessionInfo(date=today, open_time=NSE_OPEN_TIME, close_time=NSE_CLOSE_TIME)

    def session_count_in_range(self, start: date, end: date) -> int:
        count = 0
        d = start
        while d <= end:
            if self.is_trading_day(d):
                count += 1
            d += timedelta(days=1)
        return count

    def session_boundary(self, dt: datetime, tf_config: dict) -> datetime:
        tf_type = tf_config.get("type", "intraday")
        if tf_type == "intraday":
            minutes = tf_config.get("minutes", 15)
            return self._snap_intraday(dt, minutes)
        elif tf_type == "session":
            return self._snap_to_session_start(dt)
        elif tf_type == "week":
            return self._snap_to_week_start(dt)
        elif tf_type == "month":
            return self._snap_to_month_start(dt)
        return dt

    def is_shortened_session(self, d: date) -> bool:
        return d in self._special_sessions

    def _snap_intraday(self, dt: datetime, bucket_minutes: int) -> datetime:
        bucket_sec = bucket_minutes * 60
        ist_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=IST)
        day_start = ist_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        session_start = day_start.replace(hour=NSE_OPEN_TIME.hour, minute=NSE_OPEN_TIME.minute)
        ist_seconds = ist_dt.hour * 3600 + ist_dt.minute * 60 + ist_dt.second
        if ist_seconds < _NSE_OPEN_SEC:
            return session_start
        if ist_seconds >= _NSE_CLOSE_SEC:
            slots = (_NSE_CLOSE_SEC - _NSE_OPEN_SEC) // bucket_sec
            return day_start.replace(hour=0, minute=0) + timedelta(seconds=_NSE_OPEN_SEC + (slots - 1) * bucket_sec)
        elapsed = int((ist_dt - session_start).total_seconds())
        snapped = session_start + timedelta(seconds=(elapsed // bucket_sec) * bucket_sec)
        return snapped

    def _snap_to_session_start(self, dt: datetime) -> datetime:
        ist_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=IST)
        day_start = ist_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start.replace(hour=NSE_OPEN_TIME.hour, minute=NSE_OPEN_TIME.minute)

    def _snap_to_week_start(self, dt: datetime) -> datetime:
        ist_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=IST)
        monday = ist_dt.date() - timedelta(days=ist_dt.weekday())
        return datetime(monday.year, monday.month, monday.day, NSE_OPEN_TIME.hour, NSE_OPEN_TIME.minute, tzinfo=IST)

    def _snap_to_month_start(self, dt: datetime) -> datetime:
        ist_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=IST)
        first = ist_dt.date().replace(day=1)
        return datetime(first.year, first.month, first.day, NSE_OPEN_TIME.hour, NSE_OPEN_TIME.minute, tzinfo=IST)


nse_calendar = NSECalendar()
