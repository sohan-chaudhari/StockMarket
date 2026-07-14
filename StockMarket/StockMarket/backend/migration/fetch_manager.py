import time
import threading
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple

from historical_service import historical_service
from migration.config import MigrationConfig


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self):
        with self._lock:
            now = time.time()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()


class AngelOneFetchManager:
    def __init__(self, cfg: MigrationConfig):
        self._cfg = cfg
        self._rate_limiter = RateLimiter(cfg.requests_per_second)
        self._session_lock = threading.Lock()
        self._login_attempts = 0
        self._max_login_attempts = 3

    def ensure_logged_in(self) -> bool:
        with self._session_lock:
            if historical_service.is_logged_in:
                return True
            self._login_attempts = 0
            while self._login_attempts < self._max_login_attempts:
                self._login_attempts += 1
                if historical_service.login():
                    return True
                if self._login_attempts < self._max_login_attempts:
                    time.sleep(2)
            return False

    def fetch(
        self,
        ticker: str,
        angel_interval: str,
        from_date: date,
        to_date: date,
        exchange: str = "NSE",
    ) -> Tuple[bool, List[Dict], Optional[str]]:
        if not self.ensure_logged_in():
            return False, [], "Failed to log in to Angel One"

        self._rate_limiter.acquire()

        try:
            from_date_dt = datetime.combine(from_date, datetime.min.time())
            if angel_interval in ("FIVE_MINUTE", "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR"):
                from_date_dt = from_date_dt.replace(hour=9, minute=15)
            else:
                from_date_dt = from_date_dt.replace(hour=9, minute=0)

            to_date_dt = datetime.combine(to_date, datetime.min.time()).replace(hour=15, minute=30)

            candles = historical_service.get_historical_candles(
                ticker=ticker,
                interval=angel_interval,
                from_date=from_date_dt,
                to_date=to_date_dt,
                exchange=exchange,
            )

            if candles is None:
                return False, [], "API returned None"
            return True, candles, None

        except Exception as e:
            err_msg = str(e)
            if "401" in err_msg or "token" in err_msg.lower() or "session" in err_msg.lower():
                with self._session_lock:
                    historical_service.is_logged_in = False
            return False, [], err_msg

    def fetch_with_retry(
        self,
        ticker: str,
        angel_interval: str,
        from_date: date,
        to_date: date,
        exchange: str = "NSE",
    ) -> Tuple[bool, List[Dict], Optional[str]]:
        last_error = None
        for attempt in range(1, self._cfg.retry_max + 1):
            success, candles, err = self.fetch(ticker, angel_interval, from_date, to_date, exchange)
            if success:
                return True, candles, None
            last_error = err
            if attempt < self._cfg.retry_max:
                idx = min(attempt - 1, len(self._cfg.backoff_seconds) - 1)
                wait = self._cfg.backoff_seconds[idx]
                if wait > 0:
                    time.sleep(wait)
        return False, [], last_error
