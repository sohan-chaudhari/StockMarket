import time
import threading
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple

from historical_service import historical_service, HistoricalFetchError
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
    ) -> Tuple[bool, List[Dict], Optional[str], bool]:
        """Returns (success, candles, error_message, retryable). `retryable`
        is only meaningful when success is False -- fetch_with_retry uses it
        to decide whether another attempt is worth making or the failure is
        permanent (e.g. an unrecognized ticker) and retrying is pointless.

        A genuine successful response confirming zero candles for the range
        is success=True, candles=[] -- never a failure. Only a real API
        problem (rate limit, auth, network, malformed response) reaches the
        failure branch, via HistoricalFetchError raised from
        historical_service.get_historical_candles(raise_on_error=True)."""
        if not self.ensure_logged_in():
            return False, [], "Failed to log in to Angel One", True

        self._rate_limiter.acquire()

        try:
            from_date_dt = datetime.combine(from_date, datetime.min.time())
            if angel_interval in ("FIVE_MINUTE", "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR"):
                # Intraday candles are stamped from the 09:15 session open, so
                # flooring at 09:15 is correct and loses nothing.
                from_date_dt = from_date_dt.replace(hour=9, minute=15)
            else:
                # BUG FIX (found by the Phase 3 repair audit): daily/weekly/
                # monthly candles come back stamped at MIDNIGHT (00:00), not at
                # the session open. Flooring from_date at 09:00 therefore put
                # the requested first day's own candle BEFORE the window, and
                # Angel One silently omitted it -- so the first calendar day of
                # every chunk was dropped, every time.
                #
                # This was invisible until now: chunk_date_range() advances by
                # max_days + 1 = 91 days = exactly 13 weeks, so every chunk
                # start falls on the SAME weekday as the range start. The
                # original 1D batch happened to start on a Sunday, so the eight
                # dropped days were all non-trading Sundays. The repair run
                # started on Monday 2024-08-12, so the same eight drops landed
                # on real trading sessions and showed up as 13 tickers each
                # missing exactly 2024-08-12, 2024-11-11, 2025-02-10,
                # 2025-05-12, 2025-08-11, 2025-11-10, 2026-02-09, 2026-05-11.
                #
                # Midnight is the correct floor for a whole-day interval; it
                # cannot lose data, since no candle for that date sorts earlier.
                from_date_dt = from_date_dt.replace(hour=0, minute=0)

            to_date_dt = datetime.combine(to_date, datetime.min.time()).replace(hour=15, minute=30)

            candles = historical_service.get_historical_candles(
                ticker=ticker,
                interval=angel_interval,
                from_date=from_date_dt,
                to_date=to_date_dt,
                exchange=exchange,
                raise_on_error=True,
            )
            return True, candles, None, True

        except HistoricalFetchError as e:
            err_msg = str(e)
            if "401" in err_msg or "token" in err_msg.lower() or "session" in err_msg.lower():
                with self._session_lock:
                    historical_service.is_logged_in = False
            return False, [], err_msg, e.retryable
        except Exception as e:
            err_msg = str(e)
            if "401" in err_msg or "token" in err_msg.lower() or "session" in err_msg.lower():
                with self._session_lock:
                    historical_service.is_logged_in = False
            return False, [], err_msg, True

    def fetch_with_retry(
        self,
        ticker: str,
        angel_interval: str,
        from_date: date,
        to_date: date,
        exchange: str = "NSE",
    ) -> Tuple[bool, List[Dict], Optional[str], int]:
        """Returns (success, candles, error_message, attempts_used).
        attempts_used counts every call made to fetch() for this chunk,
        including the first -- so attempts_used - 1 is the number of
        retries actually performed. Callers use this to report truthful
        retry statistics instead of inferring retries indirectly."""
        last_error = None
        for attempt in range(1, self._cfg.retry_max + 1):
            success, candles, err, retryable = self.fetch(ticker, angel_interval, from_date, to_date, exchange)
            if success:
                return True, candles, None, attempt
            last_error = err
            if not retryable:
                return False, [], last_error, attempt
            if attempt < self._cfg.retry_max:
                idx = min(attempt - 1, len(self._cfg.backoff_seconds) - 1)
                wait = self._cfg.backoff_seconds[idx]
                if wait > 0:
                    time.sleep(wait)
        return False, [], last_error, self._cfg.retry_max
