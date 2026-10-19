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
import time
import threading
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


# ---- Global rate-limit backoff -------------------------------------------
# Angel One throttles the historical REST API per account ("Access denied
# because of exceeding access rate"). The retry loops elsewhere (intraday
# prefill attempts 1/2/3, dashboard index backfill) used to keep hammering while
# throttled, which held the window open and starved the ON-DEMAND chart fills --
# so a missing 09:15/09:20 opening bar could never be fetched. Once a throttle is
# seen, every historical call pauses for this long so the limit can recover.
_RATE_LIMIT_BACKOFF_SEC = 90
_rate_limit_until = 0.0

# Angel One signals a throttle in two different ways: a status=False JSON body
# ("Access denied because of exceeding access rate") AND a non-JSON body, which
# surfaces as a JSON-parse exception. Both must engage the backoff -- only the
# first was detected, which is why the backoff never fired while the app was
# being throttled ~80 times every 30 minutes.
_RATE_LIMIT_KEYWORDS = (
    'access rate', 'rate limit', 'too many requests',
    'exceeding access rate', 'throttl',
)


def _looks_rate_limited(message) -> bool:
    m = str(message or '').lower()
    return any(kw in m for kw in _RATE_LIMIT_KEYWORDS)


def _retry_after_seconds(message) -> float:
    """Honour a provider Retry-After when it is present in the error text."""
    try:
        m = str(message or '').lower()
        import re as _re
        mm = _re.search(r'retry[- _]?after["\':= ]+(\d+(?:\.\d+)?)', m)
        if mm:
            return min(float(mm.group(1)), 600.0)
    except Exception:
        pass
    return 0.0


# ===========================================================================
# SHARED PROVIDER COORDINATION  (account scope)
# ===========================================================================
# Angel One throttles the historical endpoint per ACCOUNT, and nine independent
# in-process consumers all funnel through get_historical_candles() below: chart
# open (perform_on_demand_backfill), the intraday gap sweep, the index-daily
# loop, the daily and intraday prefill jobs, the dashboard index backfill, the
# 1W/1M backfill and WS-reconnect recovery. A sleep inside each caller cannot
# coordinate them, so this module owns the ONLY gate:
#
#   * in-flight dedup  -- concurrent identical requests share a single call
#   * token bucket     -- account-wide request rate (configurable)
#   * shared cooldown  -- a throttle pauses EVERY caller, not just the one that
#                         hit it (this is what stops the retry storms)
#   * Retry-After      -- honoured when the provider supplies it
#   * bounded, jittered backoff -- never a synchronized retry stampede
#
# Configurable without code changes (env):
#   HIST_MIN_INTERVAL_SEC  default 2.0   min gap between provider calls
#   HIST_BURST             default 1     bucket depth
#   HIST_BACKOFF_SEC       default 90    cooldown after a throttle
# HIST_MIN_INTERVAL_SEC default 2.0 -> 5.0 -> 15.0
#
# Measured on production at 5.0s: 13 "Access denied because of exceeding access rate"
# rejections in 12 minutes and 6 x 90s global pauses (~9 of those 12 minutes spent
# locked out), with **0** successful repairs. The pattern was self-perpetuating:
# every ~2 minutes the pause expired, a few calls went out 5s apart, one was rejected,
# and everything was paused again -- so backfill never actually ran.
#
# A wider gap is the only way to make the SAME number of calls succeed: at 15s our
# own traffic stays under the provider's limit, so a ticker opened by a user gets its
# repair (the priority slot still lets a user repair jump the queue) instead of being
# refused. Slower overall drain, but 0 successes -> steady successes is the win.
_HISTORICAL_MIN_INTERVAL_SEC = float(os.getenv("HIST_MIN_INTERVAL_SEC", "15.0"))
_HISTORICAL_BURST = max(1, int(os.getenv("HIST_BURST", "1")))
_RATE_LIMIT_BACKOFF_SEC = float(os.getenv("HIST_BACKOFF_SEC", "90"))

_rate_limit_until = 0.0
_limiter_lock = threading.Lock()
_next_slot = 0.0            # earliest time the next call may start
_tokens = float(_HISTORICAL_BURST)

# Priority (user-facing) calls are exempt from the shared cooldown, because the
# cooldown exists to stop BACKGROUND churn and letting it also refuse the visitor's
# own bounded repair is what left CUPID showing a gap. But an UNBOUNDED exemption is
# worse: every refused call extends the provider's throttle, so a stream of
# user-triggered repairs keeps the account permanently throttled -- observed live
# (CUPID still "Access denied because of exceeding access rate" at 5s background
# pacing). This caps repair attempts account-wide so the provider can recover while
# the visitor's request still gets a real chance.
_HISTORICAL_PRIORITY_MIN_INTERVAL_SEC = float(os.getenv("HIST_PRIORITY_MIN_INTERVAL_SEC", "20"))
_priority_next_slot = 0.0


def _acquire_priority_slot() -> bool:
    """True when a user-facing repair may spend a provider call right now."""
    global _priority_next_slot
    with _limiter_lock:
        now = time.time()
        if now < _priority_next_slot:
            return False
        _priority_next_slot = now + _HISTORICAL_PRIORITY_MIN_INTERVAL_SEC
        return True

_metrics = {
    "calls": 0, "dedup_hits": 0, "throttles": 0, "cooldowns": 0,
    "errors": 0, "candles": 0,
}


def rate_limit_backoff_remaining() -> float:
    """Seconds left in the shared provider cooldown (0 when clear)."""
    return max(0.0, _rate_limit_until - time.time())


def historical_metrics() -> dict:
    with _limiter_lock:
        return dict(_metrics, cooldown_remaining=round(rate_limit_backoff_remaining(), 1),
                    min_interval=_HISTORICAL_MIN_INTERVAL_SEC, burst=_HISTORICAL_BURST)


def _note_rate_limit(message=None):
    """Start/extend the SHARED cooldown after a throttle (all callers honour it)."""
    global _rate_limit_until
    now = time.time()
    wait = _RATE_LIMIT_BACKOFF_SEC
    ra = _retry_after_seconds(message)
    if ra > 0:
        wait = max(wait, ra)
    with _limiter_lock:
        _metrics["throttles"] += 1
        if now >= _rate_limit_until:
            _metrics["cooldowns"] += 1
            print("[HISTORICAL] Rate limit hit — pausing ALL historical calls for %.0fs" % wait)
        _rate_limit_until = max(_rate_limit_until, now + wait)


def _acquire_slot():
    """Account-wide pacing: at most one call per interval (configurable)."""
    global _next_slot
    while True:
        with _limiter_lock:
            now = time.time()
            if now >= _next_slot:
                _next_slot = now + _HISTORICAL_MIN_INTERVAL_SEC
                return
            wait = _next_slot - now
        time.sleep(min(wait, 0.25))


def _throttle_historical_call():
    """Back-compat shim: pace the call (see _acquire_slot)."""
    _acquire_slot()


def _deduped(key, fn):
    """Run `fn` once for concurrent identical requests; everyone gets that result.

    Exact-key dedup only, so a waiter always receives the SAME range it asked
    for -- it can never be told that a different (e.g. narrower) background
    request has satisfied it.
    """
    global _tokens
    with _limiter_lock:
        entry = _INFLIGHT.get(key)
        owner = entry is None
        if owner:
            entry = {"ev": threading.Event(), "res": None}
            _INFLIGHT[key] = entry
        else:
            _metrics["dedup_hits"] += 1
    if not owner:
        entry["ev"].wait(timeout=180.0)
        return entry["res"]
    try:
        with _limiter_lock:
            _metrics["calls"] += 1
        entry["res"] = fn()
        return entry["res"]
    finally:
        entry["ev"].set()
        with _limiter_lock:
            _INFLIGHT.pop(key, None)


_INFLIGHT: Dict[str, dict] = {}


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


# Auth failures are neither permanent nor ordinary-transient: the session simply
# needs refreshing. Angel One invalidates one session when another is created with
# the same credentials, and this deployment shares the live credentials with the
# historical service (HISTORICAL_API_KEY == ANGELONE_API_KEY), so the two fight and
# a fetch intermittently returns "Invalid Token". Measured: 212 invalidations in 30
# minutes, i.e. about half of all fetch attempts.
#
# 'access denied' is deliberately NOT listed: "Access denied because of exceeding
# access rate" is a THROTTLE, and _looks_rate_limited() is checked first.
_AUTH_ERROR_MARKERS = (
    'invalid token', 'session expired', 'unauthorized', 'token missing',
    'authentication', 'jwt', 'not logged in',
)


def _looks_auth_error(message) -> bool:
    """True when the failure is a stale/invalid session rather than bad data."""
    m = str(message or '').lower()
    if _looks_rate_limited(m):
        return False
    return any(kw in m for kw in _AUTH_ERROR_MARKERS)


import threading

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
        
        self._login_lock = threading.Lock()
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
        Protected by _login_lock to avoid concurrent login storms.
        """
        with self._login_lock:
            if self.is_logged_in and self.smart_api:
                return True

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
        priority: bool = False,
    ) -> List[Dict]:
        """THE single entry point for Angel One history (every consumer uses this).

        Guarantees, in order:
          1. session is available (else a retryable error / empty list),
          2. the SHARED provider cooldown is clear (a throttle elsewhere pauses
             this caller too -- no consumer can bypass it),
          3. concurrent IDENTICAL requests are deduplicated: one provider call,
             and every waiter receives that same result,
          4. the account-wide pacing slot is taken by the owner only.

        `priority=True` is for a USER-REQUESTED, bounded repair (the chart-open
        fill of the current session). Such a call does NOT pre-yield to the shared
        cooldown: the cooldown exists to stop BACKGROUND churn from exhausting the
        quota, and letting it also refuse the visitor's own one-request repair is
        what left CUPID (and RELIANCE) showing a gap on open -- the request was
        logged as "skipped: shared cooldown" and the user saw missing candles. A
        throttle encountered here is still recorded, so the cooldown still applies
        to every other caller.
        """
        if not self.is_logged_in:
            msg = "Not logged in. Call login() first."
            print(f"[HISTORICAL] [ERROR] {msg}")
            if raise_on_error:
                raise HistoricalFetchError(msg, retryable=True)
            return []

        if not priority:
            _rl_left = rate_limit_backoff_remaining()
            if _rl_left > 0:
                msg = "Historical API rate-limited (shared cooldown, %.0fs left)" % _rl_left
                if raise_on_error:
                    raise HistoricalFetchError(msg, retryable=True)
                return []
        elif not _acquire_priority_slot():
            msg = ("Historical API priority slot busy (max 1 user repair per %.0fs)"
                   % _HISTORICAL_PRIORITY_MIN_INTERVAL_SEC)
            if raise_on_error:
                raise HistoricalFetchError(msg, retryable=True)
            return []

        key = ("hist", str(ticker or "").strip().upper(), str(exchange or "NSE").upper(),
               str(interval), str(from_date), str(to_date))
        return _deduped(key, lambda: self._fetch_historical_uncached(
            ticker, interval, from_date, to_date, exchange, raise_on_error, priority))

    def _fetch_historical_uncached(
        self,
        ticker: str,
        interval: str,
        from_date,
        to_date,
        exchange: str = "NSE",
        raise_on_error: bool = False,
        priority: bool = False,
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

        # Global rate-limit backoff: while Angel One is throttling us every call
        # would fail identically, and the retry loops elsewhere would hold the
        # window open. Pause instead so it can recover (and so the on-demand
        # chart fills get a chance once it does).
        # A PRIORITY call (the visitor's own bounded on-open repair) is exempt: the
        # cooldown is there to stop BACKGROUND churn, and letting it also refuse the
        # user's single request is what left CUPID showing a gap on open.
        _rl_left = rate_limit_backoff_remaining()
        if _rl_left > 0 and not priority:
            msg = "Historical API rate-limited (global backoff, %.0fs left)" % _rl_left
            if raise_on_error:
                raise HistoricalFetchError(msg, retryable=True)
            return []

        # Pace every call so the app stays under Angel One's limit.
        _throttle_historical_call()

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
                _is_rate_limit = _looks_rate_limited(error_msg)

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
                    _note_rate_limit(error_msg)
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

            _metrics["candles"] += len(candles)
            return candles

        except HistoricalFetchError:
            raise
        except Exception as e:
            print(f"[HISTORICAL] [ERROR] Error fetching candles: {e}")
            _metrics["errors"] += 1
            # A throttled call comes back as a NON-JSON body, so it lands here
            # rather than in the status=False branch below. Engage the shared
            # cooldown on this path too, otherwise the retry loops keep hammering
            # and the on-demand chart fills can never get through.
            if _looks_rate_limited(e):
                _note_rate_limit(e)
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
