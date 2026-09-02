"""
Angel One Historical Data Service
==================================
Separate service for fetching historical candle data
Uses dedicated credentials to avoid rate limit conflicts with live data

Usage:
    from historical_service import historical_service
    
    # Login
    historical_service.login()
    
    # Fetch daily candles
    candles = historical_service.get_historical_candles(
        ticker='RELIANCE',
        interval='ONE_DAY',
        from_date=date(2015, 1, 1),
        to_date=date(2019, 12, 31)
    )
"""

import os
from typing import Optional, List, Dict
from datetime import date, datetime
from SmartApi import SmartConnect
import pyotp
from dotenv import load_dotenv
import pathlib

# Load environment variables
backend_dir = pathlib.Path(__file__).parent.resolve()
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)


class HistoricalFetchError(Exception):
    """Raised by get_historical_candles(raise_on_error=True) when the API
    call itself failed (rate limit, auth, network, malformed response) --
    as opposed to a genuine successful response confirming zero candles for
    the requested range. Callers that need to distinguish "no data" from
    "the request failed" (the migration pipeline) opt into this; every
    other caller keeps the legacy return-[]-on-any-problem behavior.

    `retryable` tells the caller whether retrying is expected to help:
    False only for permanent/invalid-request conditions (e.g. an unknown
    ticker) that will fail identically on every attempt.
    """
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


# Error messages known to be permanent (retrying will not help) -- matched
# case-insensitively as substrings. Everything else (rate limits, auth,
# network, unrecognized errors) defaults to retryable=True: misclassifying
# a permanent error as retryable only costs a few wasted retries before the
# chunk is still marked FAILED explicitly, which is safe. Misclassifying a
# retryable error as permanent would give up early on something transient,
# so the default must stay retryable.
_PERMANENT_ERROR_MARKERS = ("not found", "invalid symbol", "invalid interval")


def _is_retryable_api_error(message: str) -> bool:
    m = (message or "").lower()
    return not any(marker in m for marker in _PERMANENT_ERROR_MARKERS)


class HistoricalDataService:
    """
    Dedicated service for historical data fetching
    Separate from live price service to avoid rate limit conflicts
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(HistoricalDataService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # Load HISTORICAL credentials (separate from live)
        self.api_key = os.getenv("HISTORICAL_API_KEY")
        self.client_id = os.getenv("HISTORICAL_CLIENT_ID")
        self.password = os.getenv("HISTORICAL_PASSWORD")
        self.totp_token = os.getenv("HISTORICAL_TOTP_TOKEN")
        
        # Fallback to main credentials if historical not set
        if not self.api_key or self.api_key == "your_historical_api_key_here":
            print("[HISTORICAL] Falling back to main API credentials")
            self.api_key = os.getenv("ANGELONE_API_KEY")
            self.client_id = os.getenv("ANGELONE_CLIENT_ID")
            self.password = os.getenv("ANGELONE_PASSWORD")
            self.totp_token = os.getenv("ANGELONE_TOTP_TOKEN")
        
        self.smart_api = None
        self.is_logged_in = False
        self._initialized = True
        
        print("[HISTORICAL] Historical Data Service initialized")
    
    def login(self) -> bool:
        """
        Authenticate with Angel One using HISTORICAL credentials.

        If HISTORICAL_* credentials are unset, this service falls back to the
        same account angelone_service.py uses for the live feed (see __init__).
        Calling generateSession() a second time under that same account can
        invalidate the live feed's session token -- so whenever the live
        service is already logged in under those same shared credentials,
        reuse its session instead of creating a second one. This only
        matters when both services run in the same process (the live app);
        a standalone script (e.g. the migration CLI) where angelone_service
        was never logged in falls through to the independent login below,
        unchanged.
        """
        try:
            from angelone_service import angelone_service
            if angelone_service.is_logged_in and angelone_service.smart_api \
                    and angelone_service.api_key == self.api_key:
                print("[HISTORICAL] Reusing angelone_service's live session (shared credentials) instead of a second login")
                self.smart_api = angelone_service.smart_api
                self.is_logged_in = True
                return True
        except Exception:
            pass

        if not all([self.api_key, self.client_id, self.password, self.totp_token]):
            print("[HISTORICAL] Missing credentials in .env")
            return False

        try:
            print("[HISTORICAL] Logging in to Angel One (Historical API)...")
            
            # Initialize SmartConnect
            self.smart_api = SmartConnect(api_key=self.api_key)
            
            # Generate TOTP
            totp = pyotp.TOTP(self.totp_token)
            totp_code = totp.now()
            
            # Login
            session = self.smart_api.generateSession(
                self.client_id,
                self.password,
                totp_code
            )
            
            if not session.get('status'):
                print(f"[HISTORICAL] [ERROR] Login failed: {session.get('message', 'Unknown error')}")
                return False
            
            self.is_logged_in = True
            print(f"[HISTORICAL] Login successful!")
            return True
            
        except Exception as e:
            print(f"[HISTORICAL] [ERROR] Login error: {e}")
            self.is_logged_in = False
            return False
    
    def get_historical_candles(
        self,
        ticker: str,
        interval: str,
        from_date,
        to_date,
        exchange: str = "NSE",
        raise_on_error: bool = False,
    ) -> List[Dict]:
        """
        Fetch historical candle data from Angel One

        Args:
            ticker: Stock ticker (e.g., 'RELIANCE', 'TCS')
            interval: 'ONE_DAY', 'ONE_HOUR', 'FIFTEEN_MINUTE', 'FIVE_MINUTE'
            from_date: Start date or datetime
            to_date: End date or datetime
            exchange: 'NSE' or 'BSE'
            raise_on_error: when True, an API failure (rate limit, auth,
                network, malformed response) raises HistoricalFetchError
                instead of returning []  -- so a caller that needs to tell
                "the call failed" apart from "the call succeeded and
                confirmed there's genuinely no data" can do so. Defaults to
                False so every existing caller keeps today's behavior
                unchanged; only the migration pipeline opts in.
        """
        if not self.is_logged_in:
            msg = "Not logged in. Call login() first."
            print(f"[HISTORICAL] [ERROR] {msg}")
            if raise_on_error:
                raise HistoricalFetchError(msg, retryable=True)
            return []

        # Import angelone_service to use its token lookup
        from angelone_service import angelone_service

        # Get instrument token (reuse from main service)
        ticker_clean = ticker.replace('.NS', '').replace('.BO', '')
        token_data = angelone_service.get_token(ticker_clean, exchange)

        if not token_data:
            msg = f"Ticker '{ticker}' not found"
            print(f"[HISTORICAL] [ERROR] {msg}")
            if raise_on_error:
                raise HistoricalFetchError(msg, retryable=False)
            return []

        try:
            # Prepare API params
            from_str = from_date.strftime("%Y-%m-%d %H:%M") if isinstance(from_date, datetime) else from_date.strftime("%Y-%m-%d 09:15")
            to_str = to_date.strftime("%Y-%m-%d %H:%M") if isinstance(to_date, datetime) else to_date.strftime("%Y-%m-%d 15:30")

            params = {
                "exchange": token_data.get('exchange', exchange),
                "symboltoken": token_data['token'],
                "interval": interval,
                "fromdate": from_str,
                "todate": to_str
            }

            # Call Angel One Historical API
            response = self.smart_api.getCandleData(params)

            if not response.get('status'):
                error_msg = response.get('message', 'Unknown error')
                print(f"[HISTORICAL] [ERROR] API error: {error_msg}")
                # Rate-limit errors must NOT invalidate the session — they are
                # temporary throttles, not auth failures. Treating them as
                # auth errors kills the session and forces a re-login, which
                # then also hits the rate limit and fails, leaving the session
                # broken for minutes.
                _RATE_LIMIT_KEYWORDS = (
                    'access rate', 'rate limit', 'too many requests',
                    'exceeding access rate', 'throttl',
                )
                _is_rate_limit = any(kw in error_msg.lower() for kw in _RATE_LIMIT_KEYWORDS)

                # RT-02: auth failures must invalidate the session so the next
                # caller re-logs in instead of silently returning empty data
                # for up to 12 h until the proactive timer fires.
                _AUTH_KEYWORDS = (
                    'invalid token', 'session expired', 'unauthorized',
                    'token missing', 'authentication', 'access denied',
                    'jwt', 'not logged in',
                )
                if not _is_rate_limit and any(kw in error_msg.lower() for kw in _AUTH_KEYWORDS):
                    self.is_logged_in = False
                    print("[HISTORICAL] Auth error detected — session invalidated, will re-login on next request")
                elif _is_rate_limit:
                    print("[HISTORICAL] Rate limit hit — session kept alive, caller should back off")
                if raise_on_error:
                    # A status=False response is always Angel One reporting a
                    # problem (rate limit, bad params, session issue) -- it is
                    # never how a genuine "no data in this range" is
                    # signaled (that comes back as status=True, data=[]).
                    raise HistoricalFetchError(error_msg, retryable=_is_retryable_api_error(error_msg))
                return []

            # Parse candles
            candles = []
            for candle in response.get('data', []):
                # Angel One format: [timestamp, open, high, low, close, volume]
                ts = datetime.strptime(candle[0], "%Y-%m-%dT%H:%M:%S%z")

                # Filter out weekends (Saturday=5, Sunday=6) to avoid mock trading / fake data
                if ts.weekday() >= 5:
                    continue

                candles.append({
                    'timestamp': ts,
                    'open': float(candle[1]),
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4]),
                    'volume': int(candle[5]) if candle[5] else 0
                })

            return candles

        except HistoricalFetchError:
            raise
        except Exception as e:
            print(f"[HISTORICAL] [ERROR] Error fetching candles: {e}")
            if raise_on_error:
                # Network/DNS/timeout/JSON-parse errors -- all transient.
                raise HistoricalFetchError(str(e), retryable=True)
            return []
    
    def logout(self):
        """Terminate historical API session"""
        if self.smart_api and self.is_logged_in:
            try:
                self.smart_api.terminateSession(self.client_id)
                print("[HISTORICAL] Logged out")
            except Exception as e:
                print(f"[HISTORICAL] Logout error: {e}")
            finally:
                self.is_logged_in = False


# Global singleton instance
historical_service = HistoricalDataService()


# ============================================================================
# Quick Test
# ============================================================================

if __name__ == "__main__":
    from datetime import date
    
    print("\n" + "=" * 70)
    print("TESTING HISTORICAL DATA SERVICE")
    print("=" * 70)
    
    # Login
    if historical_service.login():
        print("\n[OK] Login successful!")
        
        # Fetch 1 month of daily data for RELIANCE
        candles = historical_service.get_historical_candles(
            ticker='RELIANCE',
            interval='ONE_DAY',
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 31)
        )
        
        if candles:
            print(f"\n[OK] Fetched {len(candles)} candles")
            print("\nFirst 3 candles:")
            for candle in candles[:3]:
                print(f"  {candle['timestamp'].date()} | O:{candle['open']:.2f} "
                      f"H:{candle['high']:.2f} L:{candle['low']:.2f} "
                      f"C:{candle['close']:.2f} V:{candle['volume']:,}")
        else:
            print("\n[ERROR] No candles fetched")
        
        # Logout
        historical_service.logout()
    else:
        print("\n[ERROR] Login failed")
