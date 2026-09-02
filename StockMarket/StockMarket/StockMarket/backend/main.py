import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import logging
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# Only capture log messages at CRITICAL level (ignore yfinance ERROR noise about delisted stocks)
sentry_logging = LoggingIntegration(level=None, event_level=None)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("yfinance_downloader").setLevel(logging.WARNING)

sentry_sdk.init(
    dsn="https://8a50cc92e51241826efa3282eb43375e@o4511643888123904.ingest.de.sentry.io/4511643905753168",
    send_default_pii=True,
    traces_sample_rate=0.1,
    integrations=[sentry_logging],
    before_send=lambda event, hint: None if event.get('logger') in ('yfinance', 'yfinance_downloader') else event,
)

from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, Request, Response, BackgroundTasks

from pydantic import BaseModel

from fastapi.responses import JSONResponse

from starlette.middleware.base import BaseHTTPMiddleware

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from typing import List, Optional, Dict, Tuple

from datetime import date, timedelta, datetime, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# NSE/BSE index tickers (yfinance uses ^ prefix, not .NS)
YFINANCE_INDEX_MAP = {
    'NIFTY': '^NSEI',
    'BANKNIFTY': '^NSEBANK',
    'SENSEX': '^BSESN',
    # Global
    '^GSPC': '^GSPC',
    '^IXIC': '^IXIC',
    '^N225': '^N225',
    '^HSI': '^HSI',
    '^GDAXI': '^GDAXI',
    '^FTSE': '^FTSE',
    # India VIX
    '^INDIAVIX': '^INDIAVIX',
}
def _resolve_ticker_exchange(db: Session, clean_ticker: str) -> str:
    """Resolve a ticker's real listed exchange from stock_metadata.

    Prefers NSE when dual-listed (matches the resolution order used
    elsewhere, e.g. WS ticker validation). Falls back to "NSE" only when
    there's no metadata row to consult at all -- the same default every
    caller assumed before this function existed, so a ticker missing from
    stock_metadata sees no behavior change.
    """
    exch_rows = db.query(models.StockMetadata.exchange).filter(models.StockMetadata.ticker == clean_ticker).all()
    exchanges = {r[0] for r in exch_rows}
    return "NSE" if ("NSE" in exchanges or not exchanges) else next(iter(exchanges))


def _yfinance_ticker(clean_ticker: str, exchange: str = "NSE") -> str:
    """Map an internal clean ticker to the yfinance symbol.

    `exchange` defaults to "NSE" so every existing call site keeps its
    current behavior unchanged. Callers that know a ticker is BSE-only
    should pass exchange="BSE" -- otherwise a BSE-only ticker silently
    queries yfinance under the wrong suffix (.NS), which can return a
    different real instrument or garbage data under the same symbol
    rather than a clean "not found".
    """
    if clean_ticker in YFINANCE_INDEX_MAP:
        return YFINANCE_INDEX_MAP[clean_ticker]
    suffix = ".BO" if exchange == "BSE" else ".NS"
    return f"{clean_ticker}{suffix}"

def _is_yfinance_failed(ticker: str) -> bool:
    """Check if a ticker is in the yfinance failure cache (delisted / invalid).
    Delegates to the centralized YFinanceDownloader."""
    return yf_downloader.is_failed(ticker)

def _mark_yfinance_failed(ticker: str):
    """Add a ticker to the yfinance failure cache.
    Delegates to the centralized YFinanceDownloader with UNKNOWN error class."""
    yf_downloader._mark_failed(ticker, YFErrorClass.UNKNOWN)






def _ts_to_epoch(ts):
    """Convert an IST-naive datetime to UTC epoch seconds."""
    if ts is None: return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return int(ts.timestamp())

def _epoch_to_ist_dt(epoch_secs):
    """Convert epoch seconds to an IST-naive datetime for DB queries."""
    return datetime.fromtimestamp(epoch_secs, tz=IST).replace(tzinfo=None)

import os

import asyncio

from fastapi.middleware.cors import CORSMiddleware

# Rate Limiting

from slowapi import Limiter, _rate_limit_exceeded_handler

from slowapi.util import get_remote_address

from slowapi.errors import RateLimitExceeded

import yfinance as yf
import logging
logging.getLogger('yfinance').setLevel(logging.ERROR)

import pandas as pd

import requests

import httpx

from bs4 import BeautifulSoup

import json

import random

import secrets
import time
import threading
import concurrent.futures

from fetch_stocks import sync_market_data

from angelone_service import angelone_service

from historical_service import historical_service

from indicator_service import indicator_service

import models, schemas, database, auth
from database import get_db, get_ist_now

from routers.auth_router import router as auth_router
from routers.trade_router import router as trade_router
from price_poller import PricePoller, CriticalIndexPoller

# ── Dynamic ticker viewership manager ──
# Tracks which tickers are actively viewed across all connected clients or pinned by active orders.
# Subscribes/unsubscribes from AngelOne WS dynamically.
# Subscription invariant: ticker is subscribed iff viewer_ref_count > 0 OR execution_ref_count > 0.
# When the subscription cap is hit, evicts the least recently viewed ticker that has no active viewers
# and zero execution references (debounce expired).
class ViewedTickerManager:
    def __init__(self, angel_svc, max_subscriptions=500):
        self._angel = angel_svc
        self._max = max_subscriptions
        self._lock = threading.Lock()
        self._view_counts: Dict[str, int] = {}
        self._execution_counts: Dict[str, int] = {}
        self._pending_unsub: Dict[str, float] = {}
        self._lru: Dict[str, float] = {}  # ticker -> last view timestamp
        self._subscribed: set = set()  # tracks actual subscribed tickers (avoids count drift)
        # Stats for health endpoint
        self.stats = {
            "subscribe_calls": 0, "unsubscribe_calls": 0,
            "evictions": 0, "rejections": 0,
            "audit_repairs": 0,
        }

    def _has_interest(self, ticker: str) -> bool:
        """Returns True if ticker has at least one viewer or one active order."""
        return self._view_counts.get(ticker, 0) > 0 or self._execution_counts.get(ticker, 0) > 0

    def view(self, ticker: str):
        with self._lock:
            was_zero = self._view_counts.get(ticker, 0) == 0
            self._view_counts[ticker] = self._view_counts.get(ticker, 0) + 1
            self._pending_unsub.pop(ticker, None)
            self._lru[ticker] = time.time()
            if was_zero and ticker not in self._subscribed:
                self._try_subscribe(ticker)

    def _try_subscribe(self, ticker: str):
        """Subscribe if under cap; otherwise evict or reject."""
        if len(self._subscribed) < self._max:
            self._subscribed.add(ticker)
            self.stats["subscribe_calls"] += 1
            threading.Thread(target=self._angel.subscribe_tickers, args=([ticker],), daemon=True).start()
            return
        # Cap reached — try to evict a safe candidate
        # Safe = in pending_unsub (no active viewers) AND debounce expired (>5min) AND zero execution refs
        now = time.time()
        evictable = [(t, ts) for t, ts in self._pending_unsub.items()
                     if now - ts > 300 and not self._has_interest(t)]
        if evictable:
            evictable.sort(key=lambda x: self._lru.get(x[0], 0))
            to_evict = evictable[0][0]
            del self._pending_unsub[to_evict]
            self._lru.pop(to_evict, None)
            self._subscribed.discard(to_evict)
            self.stats["evictions"] += 1
            print(f"[ViewedTicker] EVICTED {to_evict} (LRU) for {ticker}")
            threading.Thread(target=self._angel.unsubscribe_tickers, args=([to_evict],), daemon=True).start()
            self._subscribed.add(ticker)
            self.stats["subscribe_calls"] += 1
            threading.Thread(target=self._angel.subscribe_tickers, args=([ticker],), daemon=True).start()
        else:
            self.stats["rejections"] += 1
            print(f"[ViewedTicker] REJECTED subscription to {ticker}: cap {self._max} reached, "
                  f"{len(self._pending_unsub)} pending unsubs, none expired 5min debounce (or all pinned)")

    def unview(self, ticker: str):
        with self._lock:
            cnt = self._view_counts.get(ticker, 0)
            if cnt <= 1:
                self._view_counts.pop(ticker, None)
                # Only enter debounce unsubscription if there is also NO execution interest
                if self._execution_counts.get(ticker, 0) == 0:
                    self._pending_unsub[ticker] = time.time()
            else:
                self._view_counts[ticker] = cnt - 1

    def unview_all(self, tickers: list):
        for t in tickers:
            self.unview(t)

    def set_execution_ref_counts(self, counts: Dict[str, int]):
        """
        Replaces execution reference counts from DB-authoritative truth.
        Subscribes any newly referenced tickers that are not yet subscribed.
        Enters debounce unsubscription for tickers that lost all execution interest and have no viewers.
        """
        to_subscribe = []
        with self._lock:
            # Reconcile counts from authoritative DB snapshot
            new_counts = {t: c for t, c in counts.items() if c > 0}
            old_pinned = set(self._execution_counts.keys())
            new_pinned = set(new_counts.keys())
            self._execution_counts = new_counts

            # Tickers newly acquiring execution references
            for t in new_pinned:
                self._pending_unsub.pop(t, None)
                if t not in self._subscribed:
                    if len(self._subscribed) < self._max:
                        self._subscribed.add(t)
                        to_subscribe.append(t)
                        self.stats["subscribe_calls"] += 1
                    else:
                        self._try_subscribe(t)

            # Tickers that lost all execution references
            for t in (old_pinned - new_pinned):
                if self._view_counts.get(t, 0) == 0:
                    self._pending_unsub[t] = time.time()

        if to_subscribe:
            threading.Thread(target=self._angel.subscribe_tickers, args=(to_subscribe,), daemon=True).start()

    def get_execution_ref_count(self, ticker: str) -> int:
        with self._lock:
            return self._execution_counts.get(ticker, 0)

    def get_viewed_tickers(self) -> list:
        with self._lock:
            return list(self._view_counts.keys())

    def get_all_interested_tickers(self) -> list:
        with self._lock:
            return list(set(self._view_counts.keys()) | set(self._execution_counts.keys()))

    def get_metrics(self) -> dict:
        with self._lock:
            return {
                "active_viewers": len(self._view_counts),
                "execution_pinned": len(self._execution_counts),
                "subscribed": len(self._subscribed),
                "max_subscriptions": self._max,
                "pending_unsub": len(self._pending_unsub),
                **self.stats,
                "most_viewed": sorted(self._view_counts.items(), key=lambda x: -x[1])[:10],
            }

    def audit_and_repair(self):
        """
        Consistency audit (runs every 60s).
        Subscribe-only guarantee: repairs missing subscriptions immediately.
        NEVER directly unsubscribes (delegates unsubscription safely through debounce).
        """
        to_repair = []
        with self._lock:
            for t in set(self._view_counts.keys()) | set(self._execution_counts.keys()):
                if t not in self._subscribed:
                    if len(self._subscribed) < self._max:
                        self._subscribed.add(t)
                        to_repair.append(t)
                        self.stats["audit_repairs"] += 1
                        self.stats["subscribe_calls"] += 1
                    self._pending_unsub.pop(t, None)

        if to_repair:
            print(f"[SubscriptionAudit] Repaired {len(to_repair)} missing subscriptions: {to_repair}")
            threading.Thread(target=self._angel.subscribe_tickers, args=(to_repair,), daemon=True).start()

    def _process_pending(self):
        while True:
            time.sleep(30)
            to_unsub = []
            with self._lock:
                now = time.time()
                for t, ts in list(self._pending_unsub.items()):
                    # Must have elapsed 5m debounce AND have zero viewer & zero execution interest
                    if now - ts > 300 and not self._has_interest(t):
                        to_unsub.append(t)
                for t in to_unsub:
                    del self._pending_unsub[t]
                    self._lru.pop(t, None)
                    self._subscribed.discard(t)
            if to_unsub:
                self.stats["unsubscribe_calls"] += len(to_unsub)
                print(f"[ViewedTicker] Unsubscribing {len(to_unsub)} tickers: {to_unsub}")
                threading.Thread(target=self._angel.unsubscribe_tickers, args=(to_unsub,), daemon=True).start()

viewed_ticker_mgr = None  # initialized in startup

# ── YFinance concurrent download limiter ──
# Prevents 100 concurrent yfinance calls when many users request different tickers.
_yf_semaphore = threading.BoundedSemaphore(10)
# Separate semaphore for background tasks (daily prefill, prewarm) so user requests aren't starved
_yf_bg_semaphore = threading.BoundedSemaphore(10)

def _yf_bg_download(yf_ticker: str, period: str, interval: str, timeout=8):
    """Wrapper around yf.download for background tasks (separate semaphore from user requests)."""
    acquired = _yf_bg_semaphore.acquire(blocking=True, timeout=30)
    if not acquired:
        print(f"[YFRateLimit] BG timeout waiting for semaphore for {yf_ticker}")
        return None
    try:
        return yf.download(yf_ticker, period=period, interval=interval, progress=False, auto_adjust=True, timeout=timeout)
    finally:
        _yf_bg_semaphore.release()

def _yfinance_limited_download(yf_ticker: str, period: str, interval: str, timeout=15):
    """Wrapper around yf.download with concurrency limiting."""
    acquired = _yf_semaphore.acquire(blocking=True, timeout=30)
    if not acquired:
        print(f"[YFRateLimit] Timeout waiting for semaphore for {yf_ticker}")
        return None
    try:
        return yf.download(yf_ticker, period=period, interval=interval, progress=False, auto_adjust=True, timeout=timeout)
    finally:
        _yf_semaphore.release()

app = FastAPI(title="Stock Market API")

# Cache-Control middleware: aggressive cache for assets, no-cache for HTML
class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith('/logos/') or path.endswith(('.svg', '.png', '.jpg', '.jpeg', '.gif', '.ico')):
            response.headers['Cache-Control'] = 'public, max-age=86400'
        elif path in ('/drawings.js', '/drawing-core.js', '/news.js', '/stock-ui.js', '/dashboard.js'):
            # Under active iteration — always revalidate so edits are picked up
            # on the very next reload without needing a manual ?v= bump.
            # Restore to the blanket max-age=3600 below once this stabilizes.
            response.headers['Cache-Control'] = 'no-cache'
        elif path.endswith(('.js', '.css')):
            response.headers['Cache-Control'] = 'public, max-age=3600'
        elif path.endswith('.html') or path == '/' or path == '':
            # 'private, must-revalidate' lets the browser store the page
            # in its Back/Forward Cache (bfcache) so navigating Back is
            # instant. The browser still re-validates with the server when
            # doing a fresh forward navigation.
            response.headers['Cache-Control'] = 'private, must-revalidate'
        return response

app.add_middleware(CacheControlMiddleware)

# Module-level constants (built once, not per-request)
INDEX_MAP = {
    "NIFTY":           "^NSEI",
    "SENSEX":          "^BSESN",
    "BANKNIFTY":       "^NSEBANK",
    "FINNIFTY":        "NIFTY_FIN_SERVICE.NS",
    "MIDCAP":          "^NSEMDCP50",
    "SMALLCAP":        "^NSESCP250",
    "NIFTY_AUTO":      "^CNXAUTO",
    "NIFTY_IT":        "^CNXIT",
    "NIFTY_PHARMA":    "^CNXPHARMA",
    "NIFTY_FMCG":      "^CNXFMCG",
    "NIFTY_METAL":     "^CNXMETAL",
    "NIFTY_ENERGY":    "^CNXENERGY",
    "NIFTY_MEDIA":     "^CNXMEDIA",
    "NIFTY_PSU_BANK":  "^CNXPSUBANK",
    "NIFTY_REALTY":    "^CNXREALTY",
    "NIFTY_INFRA":     "^CNXINFRA",
    "NIFTY_CPSE":      "NIFTYCPSE.NS",
    "NIFTY_MNC":       "NIFTYMNC.NS",
}
_REVERSE_INDEX_MAP = {v: k for k, v in INDEX_MAP.items()}

from fastapi.exceptions import RequestValidationError as _ReqValErr

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.exception_handler(_ReqValErr)
async def validation_exception_handler(request: Request, exc: _ReqValErr):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

from fastapi.responses import Response

@app.get("/favicon.ico", include_in_schema=False)

async def favicon():

    return Response(content=b"", media_type="image/x-icon")

# ==================== AUTH ENDPOINTS ====================

import auth

from slowapi.errors import RateLimitExceeded

from fastapi import Request

from fastapi.responses import JSONResponse

# Rate limiter -- shared instance from rate_limiter.py so routers/*.py can
# also import it without a circular import (they're imported by main.py
# before this point in the file).
from rate_limiter import limiter
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)

async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."}
    )

NSE_HOLIDAYS = set()
_holidays_lock = threading.Lock()
backfill_locks = set()
_backfill_lock = threading.Lock()

def _safe_logo(val):
    """Return logo only if it is a valid URL; otherwise empty string."""
    if not val: return ""
    if val.startswith("http://") or val.startswith("https://") or val.startswith("data:"):
        return val
    if val.startswith("logos/"):
        return val
    return ""

# --- In-memory TTL cache for yfinance results ---
_live_prices_cache: dict = {}
_live_prices_cache_lock: dict = {}
_live_prices_cache_lock_dict_mutex = threading.Lock()
LIVE_CACHE_TTL_OPEN = 60    # seconds during market hours
LIVE_CACHE_TTL_CLOSED = 3600  # 1 hour when market closed — data doesn't change
LIVE_CACHE_MAX = 100  # max entries before eviction
_ticker_last_update: dict = {}  # ticker -> timestamp of last successful price fetch

def _live_cache_ttl() -> int:
    return LIVE_CACHE_TTL_OPEN if is_market_open_now() else LIVE_CACHE_TTL_CLOSED

# --- TTL cache for yfinance failures (delisted / invalid tickers) ---
# Prevents repeated slow yfinance downloads for tickers that are known to fail
# Now delegates to the centralized YFinanceDownloader for consistent error handling
from yfinance_downloader import yf_downloader, YFErrorClass, YFinanceDownloader
_YFINANCE_FAILED: Dict[str, float] = {}  # kept for backward compat; delegates to yf_downloader
_YFINANCE_FAILED_LOCK = threading.Lock()
_YFINANCE_FAILED_TTL = 3600  # kept for backward compat

# --- TTL cache for all-stocks endpoint (rarely changes) ---
_all_stocks_cache: list = None
_all_stocks_cache_ts: float = 0
_ALL_STOCKS_CACHE_TTL = 3600  # 1 hour

# --- TTL cache for news endpoints (avoid expensive StockData queries) ---
_news_general_cache: dict = None
_news_general_cache_ts: float = 0
_news_ticker_cache: dict = {}
_NEWS_CACHE_TTL = 180  # 180 seconds

# --- AngelOne WebSocket Tick Buffer (thread-safe) ---
# AngelOne WS runs in a background thread; ticks are buffered here
# and flushed to dashboard WS clients by an asyncio task every ~200ms.
_angel_tick_buffer: Dict[str, Dict] = {}
_angel_tick_buffer_lock = threading.Lock()
_last_angel_tick_time: float = 0.0  # updated by _on_angel_tick, read by watchdog
_last_angel_ts_per_ticker: Dict[str, float] = {}  # per-ticker last tick ts for WS reconnect recovery
_last_aggregator_feed_ts: Dict[str, float] = {}  # shared dedup: last tick epoch fed to aggregator by any source

# ── Sector map (ticker → sector name) ──
_SECTOR_MAP: Dict[str, str] = {}
_sector_tickers: Dict[str, list] = {}  # sector -> list of tickers (built at startup)

# ── Monitoring counters ──
_monitoring = {
    "yfinance_requests": 0,           # total yfinance API calls
    "yfinance_cache_hits": 0,         # served from in-memory cache
    "yfinance_cache_misses": 0,       # had to fetch from yfinance
    "yfinance_semaphore_timeouts": 0, # timed out waiting for semaphore
    "yfinance_semaphore_total_waits": 0,
    "yfinance_errors": 0,             # yfinance returned error/empty
    "ws_reconnects": 0,               # WS disconnect → reconnect cycles
    "ws_replay_count": 0,             # tickers replayed after reconnect
    "rest_fallback_count": 0,         # times REST poller was sole data source
    "aggregator_ticks_processed": 0,  # total ticks fed into aggregator
    "last_reset_time": 0.0,           # when counters were last zeroed
}
_monitoring["last_reset_time"] = time.time()

# --- CSRF Protection Logic ---

def generate_csrf_token():

    return secrets.token_hex(32)

@app.get("/api/csrf-token")

def get_csrf_token_endpoint(response: Response):

    """Generate CSRF token and set cookie."""

    token = generate_csrf_token()

    # Set HttpOnly cookie for security (not accessible by JS)

    response.set_cookie(

        key="csrf_token",

        value=token,

        httponly=True,

        samesite="lax",

        secure=os.getenv("CSRF_SECURE", "false").lower() == "true",

        max_age=3600

    )

    # Return token in body (accessible by JS to send in header)

    return {"csrf_token": token}

async def validate_csrf(request: Request):
    """Dependency to validate CSRF token on specific routes."""
    csrf_cookie = request.cookies.get("csrf_token", "")
    csrf_header = request.headers.get("x-csrf-token", "")
    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail="CSRF token missing")
    if not secrets.compare_digest(csrf_cookie.encode(), csrf_header.encode()):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
    return True



@app.delete("/api/watchlist/remove")

def remove_from_watchlist(ticker: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):

    item = db.query(models.UserWatchlist).filter(

        models.UserWatchlist.user_id == current_user.user_id,

        models.UserWatchlist.ticker == ticker

    ).first()

    if not item:

        raise HTTPException(status_code=404, detail=f"'{ticker}' not found in watchlist.")

    db.delete(item)

    db.commit()

    return schemas.WatchlistRemoveResponse(message=f"{ticker} removed from watchlist")

# --- Historical API Endpoints ---

@app.get("/api/stocks-version")

def get_stocks_version():

    """

    Returns a version string used by the frontend to validate its localStorage

    stock-list cache.  The version is derived from the last daily-sync time

    (18:00 IST) so it increments once per day - no DB state needed.

    """

    now_ist = datetime.now(IST)

    # daily_sync_scheduler fires at 18:00 IST.  Before 18:00 today the

    # "last sync" was yesterday at 18:00; after 18:00 it is today.

    if now_ist.hour >= 18:

        sync_date = now_ist.date()

    else:

        from datetime import timedelta as _td

        sync_date = (now_ist - _td(days=1)).date()

    return {"version": f"{sync_date}T18:00:01"}

@app.get("/api/all-stocks")

def get_all_stocks(db: Session = Depends(get_db)):
    global _all_stocks_cache, _all_stocks_cache_ts
    now = time.time()
    if _all_stocks_cache is not None and (now - _all_stocks_cache_ts) < _ALL_STOCKS_CACHE_TTL:
        return _all_stocks_cache

    stocks = db.query(models.StockMetadata).all()

    results = []

    for s in stocks:

        results.append({

            "ticker": s.ticker,

            "name": s.name,

            "logo": _safe_logo(s.logo),

            "exchange": s.exchange,

            "basePrice": s.base_price

        })
    _all_stocks_cache = results
    _all_stocks_cache_ts = now
    return results

@app.get("/api/stock-data", response_model=List[schemas.StockDataResponse])

def get_stock_data(ticker: str, start_date: Optional[date] = None, end_date: Optional[date] = None, limit: int = 5000, db: Session = Depends(get_db)):

    # NORMALIZE

    ticker = ticker.strip().upper()

    if not ticker.startswith('^'):

        ticker = ticker.replace('.NS', '').replace('.BO', '')

    # 1. Historical

    db_tickers = [ticker]

    if '.' not in ticker and ticker not in ['NIFTY', 'BANKNIFTY', 'SENSEX']:

        db_tickers.append(f"{ticker}.NS")

    query = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers))

    if start_date: query = query.filter(models.StockData.date >= start_date)

    if end_date: query = query.filter(models.StockData.date <= end_date)

    history = query.order_by(models.StockData.date.asc()).limit(limit).all()

    # 2. Live Today

    today = database.get_ist_now().date()

    live_candle = None

    # Check if today is a trading day (weekday, not a holiday)
    # Fetch live candle on any trading day, regardless of time, to avoid
    # the gap between market close (3:30 PM) and daily EOD sync (~6 PM)

    is_trading_day = (
        today not in NSE_HOLIDAYS and
        today.weekday() < 5
    )

    if is_trading_day:

        live_candle = db.query(models.CurrentDayCandle).filter(

            models.CurrentDayCandle.ticker == ticker,

            models.CurrentDayCandle.trading_date == today

        ).first()

    # 3. Merge - Build response list

    response_list = [{
        "time": str(d.date),
        "open": d.open,
        "high": d.high,
        "low": d.low,
        "close": d.close,
        "adj_close": d.adj_close,
        "volume": _safe_int(d.volume)
    } for d in history]

    if live_candle:

        response_list = [d for d in response_list if d["time"] != str(today)]

        response_list.append({
            "time": str(live_candle.trading_date),
            "open": live_candle.open,
            "high": live_candle.high,
            "low": live_candle.low,
            "close": live_candle.current_price,
            "adj_close": live_candle.current_price,
            "volume": _safe_int(live_candle.volume)
        })

    return response_list

# Helper for Asynchronous Catch-up Backfill

def perform_on_demand_backfill(ticker: str, interval: str, backfill_start: datetime, now: datetime):

    """Background task to fetch missing intraday data without blocking the API."""

    from aggregator import snap_to_nse_session, fix_ohlc

    ticker = ticker.strip().upper()

    # 1. Lock the ticker for sequential processing

    with _backfill_lock:
        if ticker in backfill_locks:
            return
        backfill_locks.add(ticker)

    db = database.SessionLocal()
    try:

        if interval in ("1m", "5m", "15m", "30m", "1h"):
            model = models.Candle
        else:
            model = models.Candle

        # Ensure logged in

        if not historical_service.is_logged_in:

            historical_service.login()
        # Cap backfill_start to avoid API rejections (yfinance max 60d for intraday, AngelOne ~100d)
        max_days = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 730}.get(interval, 60)
        cutoff_dt = now - timedelta(days=max_days - 1)
        if backfill_start < cutoff_dt:
            print(f"[GapFill] Capping backfill_start from {backfill_start} to {cutoff_dt} due to API limits")
            backfill_start = cutoff_dt

        print(f"[Intraday] [BG] Fetching {ticker} from {backfill_start} to {now}...")
        # Try AngelOne first (unless it's an index which often lags)

        intraday_candles = []
        is_index = (ticker.upper() in ["NIFTY", "BANKNIFTY", "SENSEX", "NIFTY 50", "NIFTY BANK"])

        angel_interval_map = {
            "1m": "ONE_MINUTE",
            "5m": "FIVE_MINUTE",
            "15m": "FIFTEEN_MINUTE",
            "30m": "THIRTY_MINUTE",
            "1h": "ONE_HOUR",
            "1D": "ONE_DAY"
        }
        angel_interval = angel_interval_map.get(interval, "FIVE_MINUTE")

        exchange_val = "BSE" if ticker.upper() == "SENSEX" else "NSE"

        intraday_candles = historical_service.get_historical_candles(
            ticker=ticker,
            exchange=exchange_val,
            interval=angel_interval,
            from_date=backfill_start,
            to_date=now
        ) or []

        if not intraday_candles:
            try:
                yf_data = _fetch_yfinance_intraday(None, ticker, interval, use_bg_semaphore=True)
                if yf_data:
                    intraday_candles = []
                    for row in yf_data:
                        dt = row.timestamp
                        if backfill_start <= dt <= now:
                            intraday_candles.append({
                                "timestamp": dt,
                                "open": row.open,
                                "high": row.high,
                                "low": row.low,
                                "close": row.close,
                                "volume": row.volume
                            })
            except Exception as e:
                print(f"[GapFill] YFinance fallback failed for {ticker}: {e}")

        # === RESAMPLE FALLBACK: if API fetch failed, build from stored 5m candles ===
        if not intraday_candles and interval in ("15m", "30m", "1h"):
            try:
                from models import Candle
                from resampler import CandleResampler
                five_min_rows = db.query(Candle).filter(
                    Candle.ticker == ticker,
                    Candle.timeframe == '5m',
                    Candle.timestamp >= backfill_start
                ).order_by(Candle.timestamp.asc()).all()
                if five_min_rows:
                    five_min_dicts = [{
                        "timestamp": r.timestamp,
                        "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close),
                        "volume": int(r.volume)
                    } for r in five_min_rows]
                    resampled = CandleResampler.resample_5m_to(five_min_dicts, interval)
                    if resampled:
                        intraday_candles = resampled
                        print(f"[GapFill] Resample fallback: built {len(resampled)} {interval} candles from 5m for {ticker}")
            except Exception as e:
                print(f"[GapFill] Resample fallback failed for {ticker} {interval}: {e}")

        if intraday_candles:
            bucket_min = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(interval, 5)
            
            # Bulk fetch existing timestamps to prevent N+1 query slowdown
            existing_query = db.query(model.timestamp).filter(
                model.ticker == ticker,
                model.timestamp >= backfill_start,
                model.timestamp <= now
            )
            if hasattr(model, 'timeframe'):
                existing_query = existing_query.filter(model.timeframe == interval)
            existing_ts_set = {r.timestamp for r in existing_query.all()}
            
            for c in intraday_candles:
                ts = c.get("timestamp")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                if not ts:
                    continue
                # Snap to NSE session-aligned bucket
                epoch = _ts_to_epoch(ts)
                snapped_epoch = snap_to_nse_session(epoch, bucket_min)
                snapped_dt = _epoch_to_ist_dt(snapped_epoch)
                o = c.get("open", 0)
                h = c.get("high", 0)
                l = c.get("low", 0)
                cl = c.get("close", 0)
                v = int(c.get("volume", 0))
                o, h, l, cl = fix_ohlc(o, h, l, cl)
                
                if snapped_dt not in existing_ts_set:
                    record_kwargs = {
                        "ticker": ticker,
                        "timestamp": snapped_dt,
                        "open": o, "high": h, "low": l, "close": cl, "volume": v,
                        "timeframe": interval,
                        "is_completed": True,
                    }
                    # B-2: Use ON CONFLICT DO NOTHING instead of db.add() to eliminate
                    # the TOCTOU race where a concurrent writer (daily sync or live
                    # aggregator) inserts the same candle between our existing_ts_set
                    # snapshot and this commit, causing an IntegrityError that rolls
                    # back the entire batch.
                    from sqlalchemy.dialects.postgresql import insert as _pg_ins
                    _stmt = _pg_ins(models.Candle).values(**record_kwargs).on_conflict_do_nothing(constraint="uix_candle_key")
                    db.execute(_stmt)
            db.commit()
            print(f"[Intraday] [BG] Saved ~{len(intraday_candles)} intraday candles for {ticker}")

    except Exception as e:
        print(f"[Intraday] [BG] Error backfilling {ticker}: {e}")
        db.rollback()

    finally:
        with _backfill_lock:
            backfill_locks.discard(ticker)
        db.close()

# ==================== TRADING ENDPOINTS ====================

from trade_service import TradingService

import math

@app.post("/api/trade/place-order", dependencies=[Depends(validate_csrf)])

async def place_order(

    order: schemas.PlaceOrderRequest,

    current_user: models.User = Depends(auth.get_current_user),

    db: Session = Depends(get_db)

):

    """Open a new trading position"""

    # Fetch stock name from metadata

    stock_meta = db.query(models.StockMetadata).filter(models.StockMetadata.ticker == order.ticker).first()

    stock_name = stock_meta.name if stock_meta else order.ticker

    # ── Entry price validation & synchronization ────
    # PriceProvider resolves the authoritative current price (live tick -> forming 5m -> completed 5m -> daily close).
    from execution_engine import price_monitor
    market_price = price_monitor.get_price(order.ticker)
    if market_price is not None and market_price > 0:
        PRICE_TOLERANCE = 0.05  # ±5%
        lower_bound = market_price * (1 - PRICE_TOLERANCE)
        upper_bound = market_price * (1 + PRICE_TOLERANCE)
        # If client passed an outdated cached price outside tolerance, automatically sync to current authoritative price
        if order.entry_price < lower_bound or order.entry_price > upper_bound:
            order.entry_price = market_price

    position_type = order.position_type.upper()
    if position_type not in ("LONG", "SHORT"):
        raise HTTPException(status_code=400, detail=f"position_type must be LONG or SHORT, got {position_type}")

    position, message, amo_order = TradingService.open_position(
        db=db,
        user_id=current_user.user_id,
        ticker=order.ticker,
        position_type=position_type,
        quantity=order.quantity,
        entry_price=order.entry_price,
        take_profit=order.take_profit,
        stop_loss=order.stop_loss,
        stock_name=stock_name
    )

    if not position and not amo_order:
        raise HTTPException(status_code=400, detail=message)

    user = db.query(models.User).filter(models.User.user_id == current_user.user_id).first()
    return {
        "message": message,
        "is_amo": amo_order is not None,
        "position_id": position.id if position else None,
        "order_id": amo_order.id if amo_order else None,
        "balance": user.virtual_balance if user else 0
    }

# ==================== USER WEBSOCKET ====================

from websocket_manager import user_ws_manager

from fastapi import WebSocketDisconnect

@app.websocket("/ws/user")

async def user_websocket_endpoint(websocket: WebSocket):

    """

    WebSocket endpoint for real-time user updates (private).

    Client connects then sends auth token as first JSON message: {"token": "JWT_TOKEN"}

    """

    user_id = None

    try:

        await websocket.accept()

        # 1. Wait for auth message (5s timeout)

        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)

        token = auth_msg.get("token", "")

        payload = auth.decode_token(token)

        if not payload:

            print(f"[WS] Auth failed: Invalid token")

            await websocket.send_json({"type": "error", "message": "Authentication failed"})

            await websocket.close(code=4001)

            return

        user_id = payload.get("user_id")

        with database.SessionLocal() as db:
            user = db.query(models.User).filter(models.User.user_id == user_id).first()
            is_active = user.is_active if user else False

        if not user or not is_active:

             print(f"[WS] Auth failed: User not found or inactive")

             await websocket.send_json({"type": "error", "message": "Authentication failed"})

             await websocket.close(code=4001)

             return

        await websocket.send_json({"type": "authenticated"})

        await user_ws_manager.connect(user_id, websocket)
        try:
            while True:
                msg = await websocket.receive_json()
                await user_ws_manager.handle_message(user_id, msg)
        except WebSocketDisconnect:
            user_ws_manager.disconnect(user_id, websocket)

    except Exception as e:
        print(f"[WS User] Error: {e}")
        if user_id is not None:
            user_ws_manager.disconnect(user_id, websocket)
        try: await websocket.close()
        except: pass

@app.get("/api/portfolio/closed-positions")
async def get_closed_positions(
    page: int = 1, limit: int = 20,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    offset = (page - 1) * limit
    query = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id,
        models.Position.status == "CLOSED"
    ).order_by(models.Position.closed_at.desc())
    total = query.count()
    positions = query.offset(offset).limit(limit).all()

    # BUG-04 FIX: Closed positions don't need live prices — use stored realized_pnl and closing_price

    # DB-06: one batched query for every position's TP/SL orders instead of
    # one query per position in the loop below.
    orders_by_position = TradingService.get_orders_for_positions(db, [pos.id for pos in positions])

    result = []

    for pos in positions:

        orders = orders_by_position.get(pos.id, {"TP": None, "SL": None})
        # BUG-04 FIX: Use stored realized_pnl and closing_price — not live prices re-computed each call
        realized_pnl = pos.realized_pnl if pos.realized_pnl is not None else 0.0
        closing_price = pos.closing_price if pos.closing_price is not None else pos.entry_price
        result.append({
            "id": pos.id,
            "ticker": pos.ticker,
            "stock_name": pos.stock_name,
            "position_type": pos.position_type,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "closing_price": closing_price,
            "current_price": closing_price,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": 0.0,
            "total_investment": pos.total_investment,
            "close_type": pos.close_type,
            "created_at": pos.created_at.isoformat(),
            "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
            "tp_edit_count": pos.tp_edit_count,
            "sl_edit_count": pos.sl_edit_count,
            "take_profit": orders["TP"].trigger_price if orders["TP"] else None,
            "stop_loss": orders["SL"].trigger_price if orders["SL"] else None
        })

    return result

@app.get("/api/portfolio/transactions")
async def get_transactions(

    page: int = 1,

    limit: int = 20,

    current_user: models.User = Depends(auth.get_current_user),

    db: Session = Depends(get_db)

):

    """Get paginated transaction history"""

    offset = (page - 1) * limit

    query = db.query(models.Transaction).filter(

        models.Transaction.user_id == current_user.user_id

    ).order_by(models.Transaction.created_at.desc())

    total = query.count()

    transactions = query.offset(offset).limit(limit).all()

    result = []

    for txn in transactions:

        result.append({

            "id": txn.id,

            "position_id": txn.position_id,

            "ticker": txn.ticker,

            "stock_name": txn.stock_name,

            "transaction_type": txn.transaction_type,

            "position_type": txn.position_type,

            "quantity": txn.quantity,

            "price": txn.price,

            "amount": txn.amount,

            "pnl": txn.pnl,

            "balance_after": txn.balance_after,

            "created_at": txn.created_at.isoformat()

        })

    return {

        "transactions": result,

        "total": total,

        "page": page,

        "pages": math.ceil(total / limit) if limit > 0 else 0

    }

# ==================== ROUTER INCLUSIONS ====================

app.include_router(auth_router, prefix="/api/auth")
app.include_router(trade_router, prefix="/api/trade")

# ==================== BATCH LIVE PRICES ====================

class BatchPriceRequest(BaseModel):
    tickers: List[str]

def _safe_float(v, default=0.0):
    """Convert value to float, replacing NaN/Inf/None with default."""
    if v is None:
        return default
    try:
        if isinstance(v, pd.Series):
            v = v.iloc[-1] if not v.empty else default
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return default

def _safe_int(v, default=0):
    if v is None:
        return default
    try:
        if isinstance(v, pd.Series):
            v = v.iloc[-1] if not v.empty else default
        return int(float(v))
    except (ValueError, TypeError):
        return default

def _fmt_volume(vol):
    if not vol:
        return ''
    v = int(vol)
    if v >= 10000000:
        return f"{v / 10000000:.2f} Cr"
    if v >= 100000:
        return f"{v / 100000:.2f} L"
    return str(v)

_live_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Dedicated pool for long-running background jobs (startup prewarm, daily prefill,
# historical sync). These used to run on asyncio's DEFAULT executor via
# run_in_executor(None, ...) — the same pool asyncio.to_thread() hands request
# handlers, so a multi-minute yfinance batch would occupy every worker and stall
# ordinary requests (measured /api/market-movers at 10s+ during startup).
_bg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="bgjob")

async def fetch_batch_live_data(tickers: List[str], market_open: bool = False) -> Dict[str, Dict]:
    prices = {}
    if not tickers:
        return prices

    MAX_BATCH_SIZE = 50
    if len(tickers) > MAX_BATCH_SIZE:
        print(f"[Live] Batch too large ({len(tickers)}), truncating to {MAX_BATCH_SIZE}")
        tickers = tickers[:MAX_BATCH_SIZE]
    valid_tickers = [t for t in tickers if isinstance(t, str) and len(t) <= 50 and t.strip()]
    if len(valid_tickers) != len(tickers):
        print(f"[Live] Filtered {len(tickers) - len(valid_tickers)} invalid ticker(s)")
        tickers = valid_tickers

    # CriticalIndexPoller keeps NIFTY/SENSEX/BANKNIFTY/FINNIFTY always fresh via dedicated thread.
    # Other tickers use WS ticks or REST poller data from latest_ticks.
    with angelone_service.latest_ticks_lock:
        angel_ticks = dict(angelone_service.latest_ticks)
    remaining = []
    now_ts_sec = time.time()

    for t in tickers:
        raw = t.strip().upper()
        tick_data = angel_ticks.get(raw)

        # Check if it's an index missing OHLC data
        is_index = raw in ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCAP", "SMALLCAP"]
        missing_ohlc = is_index and (tick_data is None or tick_data.get("open", 0) == 0)

        # Use _received_ts (server receive time) for stale detection — NOT exchange timestamp.
        # This correctly handles: price hasn't moved → exchange doesn't send tick → _ts looks old.
        is_stale = False
        if tick_data and market_open:
            recv_ts = tick_data.get("_received_ts") or tick_data.get("_ts", 0)
            if isinstance(recv_ts, (float, int)) and recv_ts > 1e9:
                if now_ts_sec - recv_ts > 30:  # 30s threshold (was incorrectly 15s)
                    is_stale = True

        if tick_data and tick_data.get("current_price", 0) > 0 and not missing_ohlc and not is_stale:
            cp = tick_data["current_price"]
            pc = tick_data.get("prev_close", 0)
            change = round(cp - pc, 2) if pc else 0
            change_pct = round(((cp - pc) / pc) * 100, 2) if pc and pc != 0 else 0
            vol = tick_data.get("volume", 0)
            prices[raw] = {
                "current_price": cp,
                "current": cp,
                "open": tick_data.get("open", 0),
                "prev_close": pc,
                "high": tick_data.get("high", 0),
                "low": tick_data.get("low", 0),
                "volume": vol,
                "volume_display": _fmt_volume(vol),
                "change": change,
                "change_pct": change_pct,
                "source": tick_data.get("_source", "angel"),
            }
        else:
            remaining.append(t)

    # All tickers covered by AngelOne cache — return immediately
    if not remaining:
        now_ts = time.time()
        return prices


    # ── Step 1b: Try yfinance for remaining tickers even if AngelOne is active ──
    # (AngelOne doesn't cover NSE sector indices like NIFTY_AUTO, NIFTY_IT, etc.)

    # ── Step 2: Check in-memory TTL cache for remaining tickers ──
    now = time.time()
    ttl = _live_cache_ttl()
    still_remaining = []
    
    # Process cached items per ticker
    for tkr in remaining:
        cached = _live_prices_cache.get(tkr)
        if cached and (now - cached["ts"] < ttl):
            prices[tkr] = cached["data"]
            _ticker_last_update[tkr] = now
        else:
            still_remaining.append(tkr)
            
    if not still_remaining:
        return prices

    # We just use still_remaining directly, no locks
    final_remaining = still_remaining


    # Skip tickers in yfinance failure cache (delisted, invalid) + validate symbols
    yf_eligible = []
    skipped_failed = 0
    for t in final_remaining:
        raw = t.strip().upper()
        if _is_yfinance_failed(raw):
            skipped_failed += 1
            continue
        is_valid, _ = yf_downloader.validate_symbol(raw)
        if not is_valid:
            _mark_yfinance_failed(raw)
            skipped_failed += 1
            continue
        yf_eligible.append(t)
    if skipped_failed:
        print(f"[Live] Skipped {skipped_failed} failed/invalid tickers for yfinance")

    if not yf_eligible:
        return prices

    yf_tickers = []
    for t in yf_eligible:
        t = t.strip().upper()
        if t in INDEX_MAP:
            yf_tickers.append(INDEX_MAP[t])
        elif "FINNIFTY" in t:
            yf_tickers.append("NIFTY_FIN_SERVICE.NS")
        elif t.startswith("^"):
            yf_tickers.append(t)
        elif "." in t:
            yf_tickers.append(t)
        else:
            yf_tickers.append(f"{t}.NS")

    try:
        loop = asyncio.get_running_loop()
        now_ist = datetime.now(IST)

        def _fetch_daily():
            acquired = _yf_semaphore.acquire(blocking=True, timeout=15)
            if not acquired:
                print(f"[Live] Timeout waiting for semaphore for daily batch ({len(yf_tickers)} tickers)")
                return None
            try:
                return yf.download(
                    tickers=yf_tickers,
                    period="5d",
                    interval="1d",
                    progress=False,
                    group_by="ticker"
                )
            finally:
                _yf_semaphore.release()
        try:
            df_daily = await asyncio.wait_for(loop.run_in_executor(_live_executor, _fetch_daily), timeout=12)
        except asyncio.TimeoutError:
            print(f"[Live] yfinance daily timeout for {len(yf_tickers)} tickers")
            df_daily = None

        df_intraday = None
        if market_open:
            def _fetch_intraday():
                acquired = _yf_semaphore.acquire(blocking=True, timeout=15)
                if not acquired:
                    print(f"[Live] Timeout waiting for semaphore for intraday batch ({len(yf_tickers)} tickers)")
                    return None
                try:
                    return yf.download(
                        tickers=yf_tickers,
                        period="1d",
                        interval="5m",
                        progress=False,
                        group_by="ticker"
                    )
                finally:
                    _yf_semaphore.release()
            try:
                df_intraday = await asyncio.wait_for(loop.run_in_executor(_live_executor, _fetch_intraday), timeout=12)
            except asyncio.TimeoutError:
                print(f"[Live] yfinance intraday timeout for {len(yf_tickers)} tickers")
                df_intraday = None

        for t in remaining:
            raw = t.strip().upper()
            yf_key = INDEX_MAP.get(raw)
            if yf_key is None:
                if raw.startswith('^'):
                    yf_key = raw
                elif "FINNIFTY" in raw:
                    yf_key = "NIFTY_FIN_SERVICE.NS"
                elif "." in raw:
                    yf_key = raw
                else:
                    yf_key = f"{raw}.NS"
            try:
                # Get prev_close from daily data
                prev_close_val = 0
                if df_daily is not None and not df_daily.empty:
                    if isinstance(df_daily.columns, pd.MultiIndex):
                        if yf_key in df_daily.columns.get_level_values(0):
                            daily_col = df_daily[yf_key]
                        else:
                            daily_col = None
                    else:
                        daily_col = df_daily
                    if daily_col is not None:
                        daily_col = daily_col.dropna(how='all')
                        prev_row = daily_col.iloc[-2] if len(daily_col) > 1 else None
                        if prev_row is not None:
                            prev_close_val = _safe_float(prev_row.get("Close", prev_row.get("close", 0)))

                # Get current price from intraday data (if market open) or daily data
                close_val = 0
                open_val = 0
                high_val = 0
                low_val = 0
                vol = 0
                source_col = None
                if df_intraday is not None and not df_intraday.empty:
                    if isinstance(df_intraday.columns, pd.MultiIndex):
                        if yf_key in df_intraday.columns.get_level_values(0):
                            source_col = df_intraday[yf_key]
                    else:
                        source_col = df_intraday
                if source_col is None and df_daily is not None and not df_daily.empty:
                    if isinstance(df_daily.columns, pd.MultiIndex):
                        if yf_key in df_daily.columns.get_level_values(0):
                            source_col = df_daily[yf_key]
                    else:
                        source_col = df_daily
                if source_col is not None:
                    source_col = source_col.dropna(how='all')
                    last_row = source_col.iloc[-1] if not source_col.empty else None
                    if last_row is not None:
                        close_val = _safe_float(last_row.get("Close", last_row.get("close", 0)))
                        # When intraday (5m) data is available, compute daily OHLC across ALL bars
                        if df_intraday is not None and not df_intraday.empty:
                            first_row = source_col.iloc[0] if not source_col.empty else None
                            if first_row is not None:
                                open_val = _safe_float(first_row.get("Open", first_row.get("open", 0)))
                            if isinstance(source_col, pd.DataFrame) and "High" in source_col.columns:
                                high_val = _safe_float(source_col["High"].max())
                            elif isinstance(source_col, pd.DataFrame) and "high" in source_col.columns:
                                high_val = _safe_float(source_col["high"].max())
                            if isinstance(source_col, pd.DataFrame) and "Low" in source_col.columns:
                                low_val = _safe_float(source_col["Low"].min())
                            elif isinstance(source_col, pd.DataFrame) and "low" in source_col.columns:
                                low_val = _safe_float(source_col["low"].min())
                        else:
                            open_val = _safe_float(last_row.get("Open", last_row.get("open", 0)))
                            high_val = _safe_float(last_row.get("High", last_row.get("high", 0)))
                            low_val = _safe_float(last_row.get("Low", last_row.get("low", 0)))
                        vol = _safe_int(last_row.get("Volume", last_row.get("volume", 0)))

                if close_val > 0:
                    change = round(close_val - prev_close_val, 2) if prev_close_val else 0
                    change_pct = round(((close_val - prev_close_val) / prev_close_val) * 100, 2) if prev_close_val and prev_close_val != 0 else 0
                    prices[raw] = {
                        "current_price": close_val,
                        "current": close_val,
                        "open": open_val,
                        "prev_close": prev_close_val,
                        "high": high_val,
                        "low": low_val,
                        "volume": vol,
                        "volume_display": _fmt_volume(vol),
                        "change": change,
                        "change_pct": change_pct,
                        "_source": "yfinance",
                        "_ts": time.time(),
                    }
                else:
                    _mark_yfinance_failed(raw)
            except Exception as e:
                _mark_yfinance_failed(raw)
                print(f"[Live] Error processing {raw}: {e}")
    except Exception as e:
        print(f"[Live] fetch_batch error: {e}")

    ts_now = time.time()
    for k, v in prices.items():
        if v.get("_source") != "angel_ws" and k in final_remaining:
            _live_prices_cache[k] = {"data": v, "ts": ts_now}
            
    if len(_live_prices_cache) > LIVE_CACHE_MAX:
        with _live_prices_cache_lock_dict_mutex:
            oldest = sorted(_live_prices_cache.items(), key=lambda x: x[1]["ts"])[:len(_live_prices_cache) - LIVE_CACHE_MAX]
            for k, _ in oldest:
                del _live_prices_cache[k]

    now_ts = time.time()
    for tkr in prices:
        _ticker_last_update[tkr] = now_ts
    return prices

@app.post("/api/live-prices")
@limiter.limit("120/minute")
async def get_live_prices_batch(request: Request, req: BatchPriceRequest, current_user: Optional[models.User] = Depends(auth.get_current_user_optional)):
    _ = current_user
    prices = await fetch_batch_live_data(req.tickers, market_open=is_market_open_now())
    now_ts = time.time()
    ages = [now_ts - _ticker_last_update.get(t, 0) for t in req.tickers if t in prices]
    max_age = max(ages) if ages else 0
    prices["_market_open"] = is_market_open_now()
    prices["_data_age"] = round(max_age, 1)
    return prices

@app.get("/api/health")
def health_check():
    sub_metrics = viewed_ticker_mgr.get_metrics() if viewed_ticker_mgr else {}
    # Compute rates for counters since last reset
    uptime = time.time() - _monitoring["last_reset_time"]
    yf_req_rate = round(_monitoring["yfinance_requests"] / max(uptime, 1), 4)
    evictions_rate = round(sub_metrics.get("evictions", 0) / max(uptime, 1), 4)
    return {
        "status": "ok",
        "timestamp": datetime.now(IST).isoformat(),
        "market_open": is_market_open_now(),
        "uptime_seconds": int(uptime),
        "subscriptions": sub_metrics,
        "yfinance": {
            "max_concurrent": 3,
            "available": max(0, _yf_semaphore._value) if hasattr(_yf_semaphore, '_value') else '?',
            "requests_total": _monitoring["yfinance_requests"],
            "cache_hits": _monitoring["yfinance_cache_hits"],
            "cache_misses": _monitoring["yfinance_cache_misses"],
            "cache_hit_rate": round(_monitoring["yfinance_cache_hits"] / max(_monitoring["yfinance_cache_hits"] + _monitoring["yfinance_cache_misses"], 1), 3),
            "semaphore_timeouts": _monitoring["yfinance_semaphore_timeouts"],
            "errors": _monitoring["yfinance_errors"],
            "requests_per_sec": yf_req_rate,
        },
        "ws": {
            "reconnects": _monitoring["ws_reconnects"],
            "replays": _monitoring["ws_replay_count"],
            "last_tick_ago_secs": round(time.time() - _last_angel_tick_time, 1) if _last_angel_tick_time > 0 else None,
        },
        "aggregator": {
            "ticks_processed": _monitoring["aggregator_ticks_processed"],
            "active_candle_builders": len(candle_aggregator.active_candles) if hasattr(candle_aggregator, 'active_candles') else '?',
        },
        "evictions_per_sec": evictions_rate,
    }

# ==================== YFINANCE REPORT & SYNC ====================

@app.get("/api/yfinance/report")
def yfinance_failure_report():
    """Return the yfinance failure report with all tracked invalid/delisted symbols."""
    report = yf_downloader.get_failure_report()
    report["invalid_symbols"] = yf_downloader.get_invalid_symbols_report()
    return report

@app.post("/api/yfinance/sync-inactive")
def yfinance_sync_inactive(db: Session = Depends(get_db)):
    """Persist tracked inactive symbols to the database as is_active=False."""
    inactive = yf_downloader.get_inactive_symbols()
    updated = 0
    for ticker, reason in inactive.items():
        existing = db.query(models.StockMetadata).filter(
            models.StockMetadata.ticker == ticker
        ).first()
        if existing:
            existing.is_active = False
            updated += 1
        else:
            rec = models.StockMetadata(
                ticker=ticker, name=f"[{reason}]", exchange="NSE",
                is_active=False
            )
            db.add(rec)
            updated += 1
    if updated:
        db.commit()
        yf_downloader.clear_inactive_symbols()
    return {"synced": updated, "remaining_inactive": len(yf_downloader.get_inactive_symbols())}

# ==================== MARKET MOVERS ====================

def is_market_open_now():
    now = datetime.now(IST)
    today = now.date()
    if today.weekday() >= 5:
        return False
    with _holidays_lock:
        if today in NSE_HOLIDAYS:
            return False
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end

MOVER_TICKERS: list = []  # loaded from DB at startup (premium + indices)
ALL_WS_TICKERS: list = []  # all NSE stocks subscribed to AngelOne WS for candle building

STOCK_META = {}
try:
    meta_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "stocks_temp.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta_list = json.load(f)
        for s in meta_list:
            if s.get("ticker"):
                STOCK_META[s["ticker"]] = s
        print(f"[Movers] Loaded {len(STOCK_META)} stock metadata entries")
except Exception as e:
    print(f"[Movers] Could not load stock metadata: {e}")

@app.get("/api/aggregator-stats")
def get_aggregator_stats():
    """Diagnostic: tick ordering stats, active tickers, stale skips."""
    return candle_aggregator.get_stats()


@app.get("/api/poller-stats")
def get_poller_stats():
    """Returns stats about the multi-threaded price poller."""
    poller = getattr(app.state, "price_poller", None)
    if not poller:
        return {"status": "not_started"}
    stats = poller.get_stats()
    with angelone_service.latest_ticks_lock:
        ws_count = len(angelone_service.latest_ticks)
    return {"status": "running", "poller": stats, "ws_ticks_cached": ws_count}

# Precomputed movers snapshot. Built once at startup and refreshed on a timer by
# _movers_refresh_loop(), so /api/market-movers serves a ready dict instead of
# re-scanning ~4000 live ticks (and re-sorting three lists) on every single request.
_movers_snapshot = {"data": None, "ts": 0.0}
_movers_snapshot_lock = threading.Lock()
_MOVERS_REFRESH_SEC = 10


def _build_movers(prices: dict) -> dict:
    gainers, losers, most_active = [], [], []
    for ticker, p in prices.items():
        cp = _safe_float(p.get("current", p.get("current_price", 0)))
        open_price = _safe_float(p.get("open", 0))
        prev_close = _safe_float(p.get("prev_close", 0))
        if cp == 0 and prev_close == 0 and open_price == 0:
            continue
        change = _safe_float(p.get("change", 0))
        change_pct = _safe_float(p.get("change_pct", 0))
        vol = _safe_int(p.get("volume", 0))
        meta = STOCK_META.get(ticker, {})
        name = meta.get("name", ticker)
        logo = _safe_logo(meta.get("logo") or "")
        sector = _SECTOR_MAP.get(ticker, "")

        # Avoid showing indices as market movers, only individual stocks
        if ticker in ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCAP", "SMALLCAP", "NIFTYMIDCAP100", "NIFTYSMLCAP100"]:
            continue

        entry = {"ticker": ticker, "name": name, "logo": logo, "current_price": cp, "current": cp, "prev_close": prev_close, "open": open_price, "change": change, "change_pct": change_pct, "volume": vol, "volume_display": _fmt_volume(vol), "sector": sector}
        if change_pct >= 0.01:
            gainers.append(entry)
        elif change_pct <= -0.01:
            losers.append(entry)
        most_active.append(entry)

    gainers.sort(key=lambda x: x["change_pct"], reverse=True)
    losers.sort(key=lambda x: x["change_pct"])
    most_active.sort(key=lambda x: x["volume"], reverse=True)
    return {
        "gainers": gainers[:15], "losers": losers[:15], "most_active": most_active[:15],
        "market_open": is_market_open_now(),
        "last_updated": datetime.now(IST).isoformat(),
        "advance_count": len(gainers), "decline_count": len(losers)
    }


def _movers_prices_from_ticks() -> dict:
    """Liquidity-filtered price map from the WS tick cache. Empty when the WS has no data."""
    with angelone_service.latest_ticks_lock:
        live_ticks = dict(angelone_service.latest_ticks)
    prices = {}
    # Scan every subscribed ticker (~4000+, see ALL_WS_TICKERS at startup), not just the
    # ~216-ticker "premium" MOVER_TICKERS list — that used to make gainers/losers/most-active
    # (and the advance/decline counts derived from them) reflect only ~4% of the market.
    # The liquidity filter below still keeps illiquid/penny-stock noise out.
    for ticker, tick_data in live_ticks.items():
        cp = tick_data.get("current_price", 0)
        pc = tick_data.get("prev_close", 0)
        vol = tick_data.get("volume", 0)
        # Filter out illiquid stocks and penny stocks to provide realistic top gainers
        is_index = ticker in ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCAP", "SMALLCAP"]
        if is_index or (cp > 20 and pc > 0 and vol > 50000):
            prices[ticker] = {
                "current": cp,
                "open": tick_data.get("open", 0),
                "prev_close": pc,
                "change": cp - pc,
                "change_pct": ((cp - pc) / pc) * 100,
                "volume": vol,
            }
    return prices


async def refresh_movers_snapshot():
    """Recompute the movers snapshot. Safe to call at startup and from the timer."""
    try:
        prices = _movers_prices_from_ticks()
        if not prices:
            # WS empty (pre-market, or feed down) — fall back to a REST batch.
            prices = await fetch_batch_live_data(MOVER_TICKERS)
        data = _build_movers(prices)
        with _movers_snapshot_lock:
            _movers_snapshot["data"] = data
            _movers_snapshot["ts"] = time.time()
        return data
    except Exception as e:
        print(f"[Movers] snapshot refresh failed: {e}")
        return None


@app.get("/api/market-movers")
async def get_market_movers():
    with _movers_snapshot_lock:
        cached, ts = _movers_snapshot["data"], _movers_snapshot["ts"]
    # Serve the snapshot while it's within one refresh interval of being current.
    if cached is not None and (time.time() - ts) < _MOVERS_REFRESH_SEC * 3:
        return cached
    data = await refresh_movers_snapshot()
    if data is not None:
        return data
    return cached or {
        "gainers": [], "losers": [], "most_active": [],
        "market_open": is_market_open_now(),
        "last_updated": datetime.now(IST).isoformat(),
        "advance_count": 0, "decline_count": 0,
    }


# ==================== MARKET INTERNALS ====================
# Move-distribution histogram + 52-week high/low extremes, both computed over the
# whole live ticker universe rather than the ~200-ticker screener subset.

_52w_cache = {"data": None, "ts": 0.0}
_52w_cache_lock = threading.Lock()

# (label, min_pct_inclusive, max_pct_exclusive); None == unbounded
_DIST_BUCKETS = [
    ("<-5",   None, -5.0),
    ("-5:-3", -5.0, -3.0),
    ("-3:-1", -3.0, -1.0),
    ("-1:0",  -1.0,  0.0),
    ("0:+1",   0.0,  1.0),
    ("+1:+3",  1.0,  3.0),
    ("+3:+5",  3.0,  5.0),
    (">+5",    5.0, None),
]


def _load_52w_ranges():
    """Per-ticker 52-week high/low from daily history, cached 6h.

    Only the *range* comes from the DB. The comparison against it happens
    per-request using live WebSocket prices, so the result stays real-time even
    though the daily candle sync runs once per day.
    """
    with _52w_cache_lock:
        cached, ts = _52w_cache["data"], _52w_cache["ts"]
    if cached is not None and (time.time() - ts) < 21600:
        return cached

    ranges = {}
    try:
        from database import SessionLocal as _S
        from sqlalchemy import text as _txt
        s = _S()
        try:
            # Window is anchored to the newest date present, not CURRENT_DATE, so a
            # lagging daily sync still yields a full 12-month window.
            rows = s.execute(_txt("""
                SELECT ticker, MAX(high), MIN(low), COUNT(*)
                FROM candles
                WHERE timeframe = '1D'
                  AND timestamp >= (SELECT MAX(timestamp) FROM candles WHERE timeframe = '1D') - INTERVAL '365 days'
                  AND high > 0 AND low > 0
                GROUP BY ticker
                HAVING COUNT(*) >= 60
            """)).fetchall()
            for tkr, hi, lo, _d in rows:
                if hi and lo and float(hi) > 0 and float(lo) > 0:
                    ranges[tkr] = (float(hi), float(lo))
        finally:
            s.close()
        print(f"[Internals] Loaded 52w ranges for {len(ranges)} tickers")
    except Exception as e:
        print(f"[Internals] 52w range load failed: {e}")

    with _52w_cache_lock:
        _52w_cache["data"] = ranges
        _52w_cache["ts"] = time.time()
    return ranges


def _dist_bucket_index(pct: float) -> int:
    for i, (_label, lo, hi) in enumerate(_DIST_BUCKETS):
        if (lo is None or pct >= lo) and (hi is None or pct < hi):
            return i
    return len(_DIST_BUCKETS) - 1


def _distribution_from_db():
    """Fallback when the WS carries no ticks: latest vs previous daily close."""
    pairs = []
    try:
        from database import SessionLocal as _S
        from sqlalchemy import text as _txt
        s = _S()
        try:
            rows = s.execute(_txt("""
                WITH ranked AS (
                    SELECT ticker, close,
                           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) AS rn
                    FROM candles
                    WHERE timeframe = '1D'
                      AND timestamp >= (SELECT MAX(timestamp) FROM candles WHERE timeframe = '1D') - INTERVAL '30 days'
                      AND close > 0
                )
                SELECT a.ticker, a.close, b.close
                FROM ranked a
                JOIN ranked b ON b.ticker = a.ticker AND b.rn = 2
                WHERE a.rn = 1
            """)).fetchall()
            for tkr, cur, prev in rows:
                pairs.append((tkr, float(cur), float(prev)))
        finally:
            s.close()
    except Exception as e:
        print(f"[Internals] DB distribution fallback failed: {e}")
    return pairs


@app.get("/api/market-internals")
def get_market_internals():
    # Deliberately a sync def, not async: everything below is blocking (a lock copy and,
    # on a cold cache, a DB scan). FastAPI runs sync handlers in a threadpool, so this
    # can't stall the event loop that the WS aggregator shares.
    counts = [0] * len(_DIST_BUCKETS)
    at_high, at_low = [], []
    total = 0
    scanned_52w = 0
    source = "live"

    try:
        with angelone_service.latest_ticks_lock:
            live_ticks = dict(angelone_service.latest_ticks)

        ranges = _load_52w_ranges()
        indices = {"NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCAP", "SMALLCAP",
                   "NIFTYMIDCAP100", "NIFTYSMLCAP100"}

        pairs = []
        if live_ticks:
            for ticker, td in live_ticks.items():
                if ticker in indices:
                    continue
                cp = _safe_float(td.get("current_price", 0))
                pc = _safe_float(td.get("prev_close", 0))
                vol = _safe_int(td.get("volume", 0))
                # Much lighter floor than /api/market-movers' (cp>20, vol>50000). This is a
                # breadth statistic, so it should cover the market rather than the top ~500
                # names; the gate here only drops untraded and sub-rupee scrips, whose prints
                # would otherwise land in the tail buckets on a handful of shares.
                if cp > 5 and pc > 0 and vol > 1000:
                    pairs.append((ticker, cp, pc))
        else:
            source = "db"
            pairs = [(t, c, p) for (t, c, p) in _distribution_from_db() if t not in indices]

        for ticker, cp, pc in pairs:
            if pc <= 0:
                continue
            pct = (cp - pc) / pc * 100.0
            counts[_dist_bucket_index(pct)] += 1
            total += 1

            rng = ranges.get(ticker)
            if not rng:
                continue
            scanned_52w += 1
            hi52, lo52 = rng
            # Live price can exceed the stored range (a genuine new high), so clamp
            # the reported distance at 0 rather than showing a positive "gap".
            if cp >= hi52 * 0.98:
                at_high.append({
                    "ticker": ticker,
                    "name": STOCK_META.get(ticker, {}).get("name", ticker),
                    "price": round(cp, 2),
                    "level": round(hi52, 2),
                    "pct_from": round(min(0.0, (cp - hi52) / hi52 * 100.0), 2),
                    "change_pct": round(pct, 2),
                })
            elif cp <= lo52 * 1.02:
                at_low.append({
                    "ticker": ticker,
                    "name": STOCK_META.get(ticker, {}).get("name", ticker),
                    "price": round(cp, 2),
                    "level": round(lo52, 2),
                    "pct_from": round(max(0.0, (cp - lo52) / lo52 * 100.0), 2),
                    "change_pct": round(pct, 2),
                })

        # ── 52W scan for DB-only tickers (no live WS price) ──────────────────
        # Tickers that have a 52W range in the DB but no live tick are compared
        # using the two most-recent daily closes so the widget covers all tickers
        # with daily history, not just those currently streaming from AngelOne.
        if ranges:
            from database import SessionLocal as _S52
            from sqlalchemy import text as _txt52
            live_set = {t for t, _, _ in pairs}
            db_only_tickers = [t for t in ranges if t not in live_set and t not in indices]
            if db_only_tickers:
                try:
                    s52 = _S52()
                    try:
                        db52_rows = s52.execute(_txt52("""
                            SELECT ticker, close, prev_close FROM (
                                SELECT ticker, close,
                                       LAG(close) OVER (PARTITION BY ticker ORDER BY timestamp) AS prev_close,
                                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) AS rn
                                FROM candles
                                WHERE timeframe = '1D'
                                  AND ticker = ANY(:tickers)
                                  AND close > 0
                                  AND timestamp >= NOW() - INTERVAL '10 days'
                            ) sub WHERE rn = 1
                        """), {"tickers": db_only_tickers}).fetchall()
                    finally:
                        s52.close()
                    for tkr, db_close, db_prev in db52_rows:
                        cp = float(db_close or 0)
                        pc = float(db_prev or 0)
                        if cp < 5:
                            continue
                        rng = ranges.get(tkr)
                        if not rng:
                            continue
                        scanned_52w += 1
                        hi52, lo52 = rng
                        pct = (cp - pc) / pc * 100.0 if pc > 0 else 0.0
                        if cp >= hi52 * 0.98:
                            at_high.append({
                                "ticker": tkr,
                                "name": STOCK_META.get(tkr, {}).get("name", tkr),
                                "price": round(cp, 2),
                                "level": round(hi52, 2),
                                "pct_from": round(min(0.0, (cp - hi52) / hi52 * 100.0), 2),
                                "change_pct": round(pct, 2),
                            })
                        elif cp <= lo52 * 1.02:
                            at_low.append({
                                "ticker": tkr,
                                "name": STOCK_META.get(tkr, {}).get("name", tkr),
                                "price": round(cp, 2),
                                "level": round(lo52, 2),
                                "pct_from": round(max(0.0, (cp - lo52) / lo52 * 100.0), 2),
                                "change_pct": round(pct, 2),
                            })
                except Exception as e:
                    print(f"[Internals] DB-only 52W scan error: {e}")

        # Sort by proximity to the extreme: stocks literally AT their 52W high/low first
        at_high.sort(key=lambda x: x["pct_from"], reverse=True)   # pct_from ≤ 0; 0.0 = at high, -1.9 = 1.9% below
        at_low.sort(key=lambda x: x["pct_from"])                   # pct_from ≥ 0; 0.0 = at low, 1.9 = 1.9% above
    except Exception as e:
        print(f"[Internals] Error: {e}")

    return {
        "distribution": {
            "buckets": [
                {"label": lbl, "count": counts[i]}
                for i, (lbl, _lo, _hi) in enumerate(_DIST_BUCKETS)
            ],
            "total": total,
            "source": source,
        },
        "extremes": {
            "at_high_count": len(at_high),
            "at_low_count": len(at_low),
            "at_high": at_high,
            "at_low": at_low,
            # Stocks actually compared against a 52-week range this request — not the size
            # of the range table, which would overstate the real coverage.
            "scanned": scanned_52w,
        },
        "market_open": is_market_open_now(),
        "last_updated": datetime.now(IST).isoformat(),
    }

# ==================== SECTOR LEADERS ====================

# TTL cache for sector leaders to avoid repeated slow yfinance fallback
_sector_leaders_cache = {"data": None, "ts": 0.0}
_sector_leaders_cache_lock = threading.Lock()

@app.get("/api/sector-leaders")
def get_sector_leaders(db: Session = Depends(get_db)):
    """Return the best-performing stock in each sector based on live prices.
    Falls back to yfinance for sectors with no live WS data. Uses TTL cache (120s open / 3600s closed)
    and skips yfinance fallback if AngelOne WS has fresh ticks (covers all sectors)."""
    now = time.time()
    sect_ttl = 120 if is_market_open_now() else 3600
    with _sector_leaders_cache_lock:
        if _sector_leaders_cache["data"] is not None and now - _sector_leaders_cache["ts"] < sect_ttl:
            return _sector_leaders_cache["data"]

    leaders = {}
    sector_stocks = {}
    sectors_with_data: set = set()

    with angelone_service.latest_ticks_lock:
        live_ticks = dict(angelone_service.latest_ticks)

    # ── Step 1: fill from live WS ticks ──
    for ticker, td in live_ticks.items():
        sector = _SECTOR_MAP.get(ticker)
        if not sector:
            continue
        cp = td.get("current_price") or td.get("current", 0)
        if cp <= 0:
            continue
        change_pct = td.get("change_pct")
        if change_pct is None:
            pc = td.get("prev_close", 0)
            if pc <= 0:
                pc = candle_aggregator._previous_closes.get(ticker, 0)
            if pc <= 0:
                continue
            change_pct = round(((cp - pc) / pc) * 100, 2)
        sectors_with_data.add(sector)
        if sector not in sector_stocks:
            sector_stocks[sector] = []
        sector_stocks[sector].append({"symbol": ticker, "change": change_pct})
        if sector not in leaders or change_pct > leaders[sector]["change"]:
            meta = STOCK_META.get(ticker, {})
            leaders[sector] = {"symbol": ticker, "name": meta.get("name", ticker), "change": change_pct}

    # ── Step 2: yfinance fallback only if AngelOne WS has no fresh ticks ──
    ws_fresh = (_last_angel_tick_time > 0 and time.time() - _last_angel_tick_time < 30)
    missing_sectors = [s for s in _sector_tickers if s not in sectors_with_data]
    if missing_sectors and not ws_fresh:
        def _fetch_sector_yf(sector: str, tickers: list):
            for tkr in tickers[:3]:
                try:
                    df = _yf_bg_download(_yfinance_ticker(tkr), period="2d", interval="1d", timeout=4)
                    if df is not None and not df.empty:
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        row = df.iloc[-1]
                        close = float(row.get('Close', 0) or 0)
                        if len(df) >= 2:
                            prev_close = float(df.iloc[-2].get('Close', 0) or 0)
                        else:
                            prev_close = float(row.get('Open', 0) or 0)
                        if close > 0 and prev_close > 0:
                            change_pct = round(((close - prev_close) / prev_close) * 100, 2)
                            return (sector, tkr, change_pct)
                except Exception:
                    continue
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_map = {executor.submit(_fetch_sector_yf, s, _sector_tickers[s]): s for s in missing_sectors}
            done, _ = concurrent.futures.wait(fut_map.keys(), timeout=5)
            for fut in done:
                result = fut.result()
                if result is not None:
                    sector, tkr, change_pct = result
                    if sector not in leaders:
                        meta = STOCK_META.get(tkr, {})
                        leaders[sector] = {"symbol": tkr, "name": meta.get("name", tkr), "change": change_pct}
                        sector_stocks[sector] = [{"symbol": tkr, "change": change_pct}]

    result = []
    for sector in sorted(leaders.keys()):
        entry = leaders[sector]
        stock_list = sector_stocks.get(sector, [])
        stock_list.sort(key=lambda x: -abs(x["change"]))
        entry["top5"] = stock_list[:5]
        entry["sector"] = sector
        result.append(entry)

    with _sector_leaders_cache_lock:
        _sector_leaders_cache["data"] = result
        _sector_leaders_cache["ts"] = time.time()

    return result

# ==================== NEWS & SENTIMENT ====================

# --------------- Direct ScanX news search (bypasses port 8003) ---------------
_vader_analyzer = None
def _get_vader_analyzer():
    global _vader_analyzer
    if _vader_analyzer is None:
        try:
            import nltk
            from nltk.sentiment.vader import SentimentIntensityAnalyzer
            _vader_analyzer = SentimentIntensityAnalyzer()
            # Comprehensive Stock Market & Financial News Lexicon
            financial_lexicon = {
                # Growth & Expansion (Bullish)
                "partnership": 2.2, "partners": 2.0, "partner": 2.0, "partnering": 2.0,
                "launch": 2.0, "launches": 2.2, "launched": 2.2, "launching": 2.0,
                "accelerate": 2.0, "accelerates": 2.2, "accelerating": 2.0, "adoption": 1.8,
                "scale": 1.8, "scaling": 1.8, "expansion": 2.2, "expands": 2.2, "expanding": 2.2,
                "deal": 2.0, "deals": 2.0, "contract": 1.8, "contracts": 1.8, "multimillion": 2.8,
                "order": 1.5, "orders": 1.5, "awarded": 2.5, "wins": 2.8, "won": 2.8, "win": 2.5,
                "acquisition": 2.0, "acquires": 2.2, "acquired": 2.2, "merger": 1.8,
                "innovation": 2.0, "transformation": 2.0, "transform": 1.8, "breakthrough": 2.8,
                
                # Earnings & Performance (Bullish)
                "rises": 2.4, "rise": 2.2, "rising": 2.2, "jumped": 2.5, "jumps": 2.5,
                "surge": 2.8, "surges": 2.8, "surged": 2.8, "surging": 2.8,
                "profit": 2.5, "profits": 2.5, "profitable": 2.2, "profitability": 2.2,
                "growth": 2.2, "gain": 2.0, "gains": 2.0, "gained": 2.0, "gaining": 2.0,
                "rally": 2.2, "rallies": 2.2, "all-time high": 3.2, "record": 2.0, "high": 1.5,
                "dividend": 2.0, "bonus": 2.2, "buyback": 2.5, "upgrade": 2.5, "upgrades": 2.5, "upgraded": 2.5,
                "outperform": 2.8, "beat": 2.4, "beats": 2.4, "beating": 2.2, "bullish": 2.8,
                "revenue": 1.5, "ebitda": 1.5, "margin": 1.2, "margins": 1.5, "milestone": 2.2,

                # Declines & Downturns (Bearish)
                "fell": -2.4, "falls": -2.4, "falling": -2.2, "fall": -2.0,
                "plunges": -3.0, "plunged": -3.0, "plunge": -2.8, "plunging": -2.8,
                "drop": -2.0, "drops": -2.0, "dropped": -2.0, "dropping": -2.0,
                "loss": -2.5, "losses": -2.5, "losing": -2.0, "slump": -2.8, "slumps": -2.8, "slumped": -2.8,
                "downgrade": -2.8, "downgrades": -2.8, "downgraded": -2.8,
                "underperform": -2.8, "miss": -2.2, "misses": -2.2, "missed": -2.2,
                "bearish": -2.8, "fraud": -3.8, "default": -3.8, "defaults": -3.8, "defaulted": -3.8,
                "crash": -3.5, "crashes": -3.5, "crashed": -3.5, "crashing": -3.5,
                "probe": -2.5, "investigation": -2.5, "penalty": -2.5, "penalized": -2.5,
                "fine": -2.0, "fined": -2.2, "ban": -3.0, "banned": -3.0, "scam": -4.0,
                "debt": -1.8, "layoff": -2.8, "layoffs": -2.8, "fire": -2.0, "fired": -2.2
            }
            _vader_analyzer.lexicon.update(financial_lexicon)
        except Exception:
            _vader_analyzer = False
    return _vader_analyzer if _vader_analyzer is not False else None

_FINANCIAL_LEXICON: dict = {
    # Bullish / Growth / Positive Financial Keywords
    "gain": 2.2, "gains": 2.2, "gained": 2.0, "gaining": 2.0,
    "rises": 2.4, "rise": 2.2, "rising": 2.2, "rose": 2.2,
    "jumped": 2.5, "jumps": 2.5, "jumping": 2.2,
    "surge": 2.8, "surges": 2.8, "surged": 2.8, "surging": 2.8,
    "rally": 2.2, "rallied": 2.2, "rallies": 2.2, "rallying": 2.2,
    "profit": 2.5, "profits": 2.5, "profitable": 2.2, "profitability": 2.2,
    "growth": 2.2, "grow": 2.0, "grows": 2.0, "growing": 2.0,
    "record": 2.0, "dividend": 2.2, "dividends": 2.2, "buyback": 2.5,
    "upgrade": 2.5, "upgraded": 2.5, "upgrades": 2.5,
    "outperform": 2.8, "outperformed": 2.8, "outperforming": 2.8,
    "beat": 2.4, "beats": 2.4, "beating": 2.2, "beaten": 1.5,
    "bullish": 2.8, "milestone": 2.2, "deal": 2.0, "deals": 2.0,
    "win": 2.5, "wins": 2.8, "won": 2.8, "winning": 2.5,
    "expansion": 2.2, "expands": 2.2, "expanded": 2.2, "expanding": 2.2,
    "launch": 2.0, "launches": 2.2, "launched": 2.2, "launching": 2.0,
    "partnership": 2.2, "partner": 2.0, "partners": 2.0, "partnering": 2.0,
    "acquisition": 2.0, "acquires": 2.2, "acquired": 2.2, "acquiring": 2.0,
    "breakout": 2.5, "soars": 2.8, "soared": 2.8, "soaring": 2.8, "soar": 2.5,
    "recovery": 2.0, "recover": 2.0, "recovered": 2.0, "rebound": 2.2, "rebounds": 2.2,
    "higher": 1.8, "highest": 2.2, "high": 1.5, "strong": 2.0, "stronger": 2.2, "strongest": 2.4,
    "positive": 1.8, "order": 1.5, "orders": 1.8, "ordered": 1.2,
    "bonus": 2.2, "innovation": 1.8, "innovative": 1.8, "breakthrough": 2.8,
    "awarded": 2.5, "awards": 2.2, "unveils": 2.2, "unveiled": 2.2, "unveil": 2.0,
    "earn": 1.8, "earns": 2.0, "earned": 2.0, "earning": 1.8, "earnings": 2.2,
    "investors": 1.5, "investor": 1.5, "investment": 1.8, "invests": 1.8, "invested": 1.8,
    "scale": 1.5, "scaling": 1.8, "scaled": 1.8, "advances": 2.0, "advance": 1.8, "advanced": 1.8,
    "exceeds": 2.4, "exceeded": 2.4, "inflows": 2.2, "inflow": 2.0,
    "up": 1.5, "boost": 2.2, "boosts": 2.2, "boosted": 2.2, "boosting": 2.2,
    "top": 1.5, "uptrend": 2.2, "upturn": 2.0, "deliverable": 1.5, "revenue": 1.8,
    "hike": 1.8, "hikes": 1.8, "hiked": 1.8, "multimillion": 2.5,

    # Bearish / Decline / Negative Financial Keywords
    "fell": -2.4, "falls": -2.4, "fall": -2.0, "falling": -2.2,
    "plunges": -3.0, "plunged": -3.0, "plunge": -2.8, "plunging": -2.8,
    "drop": -2.0, "drops": -2.0, "dropped": -2.0, "dropping": -2.0,
    "loss": -2.5, "losses": -2.5, "lost": -2.2, "losing": -2.0,
    "slump": -2.8, "slumped": -2.8, "slumps": -2.8, "slumping": -2.8,
    "downgrade": -2.8, "downgraded": -2.8, "downgrades": -2.8,
    "underperform": -2.8, "underperformed": -2.8,
    "miss": -2.2, "misses": -2.2, "missed": -2.2, "missing": -2.0,
    "bearish": -2.8, "fraud": -3.8, "default": -3.8, "defaults": -3.8, "defaulted": -3.8,
    "crash": -3.5, "crashes": -3.5, "crashed": -3.5, "crashing": -3.5,
    "probe": -2.5, "probed": -2.5, "investigation": -2.5, "investigated": -2.5,
    "penalty": -2.5, "penalties": -2.5, "penalized": -2.5,
    "fine": -2.0, "fined": -2.2, "fines": -2.0,
    "ban": -3.0, "banned": -3.0, "banning": -3.0, "bans": -3.0,
    "scam": -4.0, "debt": -1.8, "debts": -1.8,
    "layoff": -2.8, "layoffs": -2.8, "laid": -2.5,
    "concern": -1.5, "concerns": -1.8, "concerned": -1.5,
    "risk": -1.2, "risks": -1.5, "risky": -1.5,
    "warning": -2.0, "warns": -2.2, "warned": -2.2, "warn": -2.0,
    "decline": -2.0, "declines": -2.0, "declined": -2.0, "declining": -2.0,
    "lower": -1.5, "lowest": -2.0, "low": -1.2,
    "weak": -1.8, "weakness": -1.8, "weaker": -2.0, "weakest": -2.2,
    "pressure": -1.5, "pressures": -1.5, "pressured": -1.5,
    "tumbles": -2.5, "tumbled": -2.5, "tumble": -2.2, "tumbling": -2.5,
    "reduce": -1.8, "reduces": -2.0, "reduced": -2.0, "reducing": -1.8, "reduction": -1.8,
    "sale": -1.2, "sell": -1.5, "selling": -1.5, "sold": -1.5, "sells": -1.5,
    "cut": -2.0, "cuts": -2.0, "cutting": -2.0,
    "outflows": -2.2, "outflow": -2.0, "down": -1.5, "downtrend": -2.2, "downturn": -2.0,
    "dip": -1.2, "dips": -1.2, "dipped": -1.2,
}

def _calculate_news_sentiment(text: str) -> dict:
    """
    Keyword-based financial sentiment scorer. Works with or without NLTK/VADER.
    Returns {"label": "Bullish"|"Bearish"|"Neutral", "sentiment_score": float -1..1}
    """
    if not text:
        return {"label": "Neutral", "sentiment_score": 0.0}

    # Try VADER first if available
    analyzer = _get_vader_analyzer()
    if analyzer:
        try:
            scores = analyzer.polarity_scores(text)
            compound = round(scores["compound"], 2)
            label = "Bullish" if compound >= 0.05 else ("Bearish" if compound <= -0.05 else "Neutral")
            return {"label": label, "sentiment_score": compound}
        except Exception:
            pass

    # Pure-Python keyword fallback with negation detection
    import re as _re
    _NEG_WORDS  = {'not', 'no', 'never', 'without', 'lack', 'lacking', 'fails', 'failed', 'neither', 'nor'}
    _NEG_VERBS  = {'decline', 'declined', 'declines', 'fall', 'falls', 'fell', 'falling',
                   'drop', 'drops', 'dropped', 'slump', 'slumped', 'plunge', 'plunged',
                   'miss', 'misses', 'missed', 'warn', 'warned', 'concern', 'concerns',
                   'tumble', 'tumbled', 'crash', 'crashed', 'weak', 'weakness', 'loss', 'losses',
                   'reduce', 'reduced', 'cut', 'cuts'}
    words = _re.sub(r'[^\w\s]', ' ', text.lower()).split()
    total = 0.0
    for i, w in enumerate(words):
        weight = _FINANCIAL_LEXICON.get(w, 0.0)
        if weight == 0.0:
            continue
        # Window of ±4 words around this keyword
        window = set(words[max(0, i - 4):i] + words[i + 1:min(len(words), i + 5)])
        if weight > 0 and (window & _NEG_WORDS or window & _NEG_VERBS):
            weight = -weight * 0.6   # flip positive → negative with dampening
        total += weight

    if words:
        total = total / max(len(words) ** 0.5, 1)
    compound = round(max(-1.0, min(1.0, total / 3.0)), 2)
    label = "Bullish" if compound >= 0.04 else ("Bearish" if compound <= -0.04 else "Neutral")
    return {"label": label, "sentiment_score": compound}


_news_search_cache: dict = {}   # key → {data, ts}
_NEWS_SEARCH_TTL = 300          # 5-minute cache

# Domains that publish high-quality Indian financial news
_FINANCE_SOURCES = {
    "economictimes", "moneycontrol", "livemint", "businessstandard",
    "ndtvprofit", "cnbctv18", "thehindu", "timesofindia", "financialexpress",
    "business-standard", "thehindubusinessline", "zeebiz", "bloombergquint",
    "reuters", "bloomberg", "marketsmojo", "tickertape",
}

def _is_nav_or_homepage(title: str, link: str) -> bool:
    """Return True if this looks like a navigation or homepage entry (not an article)."""
    import re as _re
    # Exclude entries with no title or extremely short titles (homepage tags)
    if not title or len(title) < 15:
        return True
    nav_patterns = [
        r'^ScanX\s*[-–]',
        r'^Latest (Market|Global|Business) News\s*[-–]',
        r'^Stock Screener',
        r'^Share Market Live',
    ]
    for p in nav_patterns:
        if _re.search(p, title, _re.IGNORECASE):
            return True
    return False

@app.get("/api/news/search/{query}")
async def news_search(query: str, limit: int = 20):
    """
    Fetch real Indian stock market news exclusively from scanx.trade via Google News RSS.
    For market queries: searches Nifty/Sensex/NSE market news on scanx.trade.
    For ticker queries: searches scanx.trade for the ticker and company name.
    Results are cached 5 minutes.
    """
    import feedparser
    import re as _re
    from urllib.parse import quote_plus

    q_clean = query.strip()
    cache_key = f"{q_clean.lower()}:{limit}"
    cached = _news_search_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _NEWS_SEARCH_TTL:
        return cached["data"]

    base_url = "https://news.google.com/rss/search"
    is_market = q_clean.lower() in ("market", "nse", "bse", "nifty", "sensex", "all")

    if is_market:
        rss_queries = [
            "site:scanx.trade (market OR Nifty OR Sensex OR shares OR stock)",
            "site:scanx.trade",
        ]
    else:
        meta_name = (STOCK_META.get(q_clean.upper()) or {}).get("name", "")
        # Clean company name
        cname = meta_name
        for s in [r'\s+Limited$', r'\s+Ltd\.?$', r'\s+Corporation$', r'\s+Corp\.?$', r'\s+Private$', r'\s+Pvt\.?$']:
            cname = _re.sub(s, '', cname, flags=_re.IGNORECASE) if cname else ""
        cname = cname.strip() if cname else ""

        if cname and cname.upper() != q_clean.upper():
            rss_queries = [
                f'site:scanx.trade ({q_clean.upper()} OR "{cname}")',
                f'site:scanx.trade {q_clean.upper()}',
                f'site:scanx.trade "{cname}"',
                "site:scanx.trade",
            ]
        else:
            rss_queries = [
                f'site:scanx.trade {q_clean.upper()}',
                f'site:scanx.trade {q_clean}',
                "site:scanx.trade",
            ]

    seen_urls: set = set()
    articles: list = []

    def _is_nav_or_quote(t: str) -> bool:
        if not t or len(t) < 15:
            return True
        nav_patterns = [
            r'^(ScanX|Scanx)\s*[-–]',
            r'^Latest (Market|Global|Business) News\s*[-–]',
            r'Stock Screener',
            r'Share Market Live',
            r'Heatmap',
            r'Market Valuation',
            r'Financial Summary',
            r'Share Price Today\b',
            r'^Mutual Funds',
            r'^Bulk/Block Deals',
            r'Custom Stock Screener',
            r'Sensex Stocks$',
            r'Nifty Bank Stocks$',
        ]
        for p in nav_patterns:
            if _re.search(p, t, _re.IGNORECASE):
                return True
        return False

    def _parse_rss_queries():
        import requests as _req
        _arts = []
        _seen = set()
        for rq in rss_queries:
            try:
                rss_url = f"{base_url}?q={quote_plus(rq)}&hl=en-IN&gl=IN&ceid=IN:en"
                _r = _req.get(rss_url, timeout=8,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; StockApp/1.0)"})
                feed = feedparser.parse(_r.text)
                for entry in feed.entries:
                    title = (getattr(entry, "title", "") or "").strip()
                    link  = (getattr(entry, "link",  "") or "").strip()
                    pub   = getattr(entry, "published", "") or ""
                    desc  = getattr(entry, "description", "") or ""
                    source_elem = getattr(entry, "source", {})
                    source_title = (source_elem.get("title", "") if isinstance(source_elem, dict) else str(getattr(entry, "source", ""))).lower()

                    if not title or len(title) < 15 or link in _seen:
                        continue
                    if _is_nav_or_quote(title):
                        continue

                    # STRICT FILTER: Only accept articles originating from scanx.trade
                    if "scanx.trade" not in link.lower() and "scanx" not in source_title and "scanx.trade" not in rq:
                        continue

                    _seen.add(link)
                    clean_title = _re.sub(r'\s*[-–|]\s*scanx\.trade\s*$', '', title, flags=_re.IGNORECASE).strip()
                    clean_title = _re.sub(r'\s*[-–|]\s*[\w\s\.]+$', '', clean_title).strip() or clean_title
                    clean_excerpt = _re.sub(r'<[^>]+>', '', desc)[:280]
                    sentiment_obj = _calculate_news_sentiment(f"{clean_title}. {clean_excerpt}")
                    _arts.append({
                        "title":        clean_title,
                        "excerpt":      clean_excerpt,
                        "url":          link,
                        "source":       "ScanX",
                        "published_at": pub,
                        "ticker":       "MARKET" if is_market else q_clean.upper(),
                        "sentiment":    sentiment_obj,
                    })
            except Exception as e:
                print(f"[News Search] query='{rq}': {e}")
            if len(_arts) >= limit:
                break
        return _arts[:limit]

    # Run the blocking feedparser calls in a thread so we don't block the event loop
    articles = await asyncio.to_thread(_parse_rss_queries)
    _news_search_cache[cache_key] = {"data": articles, "ts": time.time()}
    return articles


# --------------- Proxy to News Sentiment service (port 8003) ---------------
NEWS_SENTIMENT_BASE = "http://127.0.0.1:8003"

@app.get("/api/scanx/news/full/all")
async def proxy_scanx_news_all(limit: int = 20):
    """Proxy: forward general ScanX news request to the News Sentiment service.
    The News Sentiment app mounts scanx_news router under /api, so path is /api/scanx/...
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{NEWS_SENTIMENT_BASE}/api/scanx/news/full/all",
                params={"limit": limit}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"[Proxy /api/scanx/news/full/all] Error reaching News Sentiment service: {e}")
        return []


@app.get("/api/scanx/news/full/{ticker}")
async def proxy_scanx_news_ticker(ticker: str, limit: int = 20):
    """Proxy: forward ticker-specific ScanX news request to the News Sentiment service."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{NEWS_SENTIMENT_BASE}/api/scanx/news/full/{ticker.upper()}",
                params={"limit": limit}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"[Proxy /api/scanx/news/full/{ticker}] Error reaching News Sentiment service: {e}")
        return []

@app.get("/api/scanx/news/fast/{ticker}")
async def proxy_scanx_news_fast_ticker(ticker: str, limit: int = 20):
    """Proxy: forward fast ticker-specific ScanX news request to the News Sentiment service."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # We don't use upper() here to preserve lowercase if it matters for the RSS query
            resp = await client.get(
                f"{NEWS_SENTIMENT_BASE}/api/scanx/news/fast/{ticker}",
                params={"limit": limit}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"[Proxy /api/scanx/news/fast/{ticker}] Error reaching News Sentiment service: {e}")
        return []
# ---------------------------------------------------------------------------

@app.get("/api/scanx/news/market-sentiment")
async def proxy_market_sentiment(db: Session = Depends(get_db)):
    try:
        # Generate sentiment from actual live market data
        try:
            live = await fetch_batch_live_data(['NIFTY', 'SENSEX', 'BANKNIFTY'])
            live_scores = []
            for t in ['NIFTY', 'SENSEX', 'BANKNIFTY']:
                p = live.get(t, {})
                cp = _safe_float(p.get('current_price') or p.get('current'))
                op = _safe_float(p.get('open') or 0)
                prev = _safe_float(p.get('prev_close')) or op
                if cp and prev and prev > 0:
                    live_scores.append(((cp - prev) / prev) * 100)
            
            if live_scores:
                avg = sum(live_scores) / len(live_scores)
                label = "positive" if avg > 0.3 else ("negative" if avg < -0.3 else "neutral")
                score = max(0, min(100, 50 + int(avg * 10)))
                summary = f"Markets are {label} based on live prices (avg change {avg:+.2f}%)."
                return {"sentiment": label, "label": label, "score": score, "summary": summary}
        except Exception as e:
            print(f"[Sentiment] Live fetch failed: {e}")

        # Fallback to last available trading day if live data is completely down
        today = date.today()
        row = db.query(models.Candle).filter(
            models.Candle.ticker == 'NIFTY',
            models.Candle.timeframe == '1D',
        ).order_by(models.Candle.timestamp.desc()).limit(2).all()

        if row and len(row) > 0:
            current_day = row[0]
            prev_close = row[1].close if len(row) > 1 else current_day.open
            if current_day.close and prev_close and prev_close > 0:
                chg_pct = ((current_day.close - prev_close) / prev_close) * 100
                label = "positive" if chg_pct > 0.3 else ("negative" if chg_pct < -0.3 else "neutral")
                score = max(0, min(100, 50 + int(chg_pct * 10)))
                summary = f"Based on last trading day ({current_day.timestamp.date()}), market sentiment is {label}."
                return {"sentiment": label, "label": label, "score": score, "summary": summary}

        return {"sentiment": "neutral", "label": "neutral", "score": 50, "summary": "Market data is being processed."}
    except Exception as e:
        print(f"[Sentiment] Error: {e}")
        return {"sentiment": "neutral", "label": "neutral", "score": 50, "summary": "Market sentiment temporarily unavailable", "error": str(e)}

@app.get("/api/v1/news/google-rss")
def google_news_rss(q: str):
    import requests
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        resp = requests.get(url, timeout=5)
        return Response(content=resp.text, media_type="application/xml")
    except Exception as e:
        return Response(content=f"<error>{str(e)}</error>", status_code=500, media_type="application/xml")

@app.get("/api/news/general")
async def proxy_news_general(db: Session = Depends(get_db)):
    global _news_general_cache, _news_general_cache_ts
    now_ts = time.time()
    if _news_general_cache is not None and (now_ts - _news_general_cache_ts) < _NEWS_CACHE_TTL:
        return _news_general_cache
    try:
        articles = []
        ticker_list = list(dict.fromkeys(DASHBOARD_TICKERS + MOVER_TICKERS))
        ticker_list = [t for t in ticker_list if t not in ['NIFTY', 'SENSEX', 'BANKNIFTY', 'FINNIFTY']][:25]
        
        with angelone_service.latest_ticks_lock:
            live_ticks = dict(angelone_service.latest_ticks)
            
        today_date = datetime.now(IST).strftime("%Y-%m-%d")

        def fetch_latest():
            res = {}
            rows = db.query(models.Candle).filter(
                models.Candle.ticker.in_(ticker_list),
                models.Candle.timeframe == '1D',
            ).distinct(models.Candle.ticker).order_by(
                models.Candle.ticker, models.Candle.timestamp.desc()
            ).all()
            for row in rows:
                res[row.ticker] = row
            return res

        latest_by_ticker = fetch_latest()

        # ── Stock-level articles with sector context ──
        stock_changes = []
        has_data = False
        for t in ticker_list:
            n = (STOCK_META.get(t) or {}).get('name', t)
            sector = _SECTOR_MAP.get(t, "")
            sector_tag = f" ({sector})" if sector else ""
            
            # Prefer Live data
            tick = live_ticks.get(t)
            if tick and tick.get('last_traded_price') and tick.get('change_per'):
                has_data = True
                chg = tick['change_per']
                cp = tick['last_traded_price']
                high = tick.get('high', cp)
                low = tick.get('low', cp)
                vol = tick.get('volume_trade_for_the_day', 0)
                
                direction = "gained" if chg >= 0 else "lost"
                stock_changes.append((t, n, sector, chg, cp))
                articles.append({
                    "title": f"{n}{sector_tag} {direction} {abs(chg):.2f}% on {today_date}",
                    "summary": f"{n} traded at ₹{cp:.2f} | High: ₹{high:.2f} Low: ₹{low:.2f} | Vol: {int(vol):,}",
                    "sentiment": "positive" if chg >= 0 else "negative",
                    "source": "Live Market Data",
                    "ticker": t,
                    "url": "",
                    "published_at": today_date
                })
            else:
                row = latest_by_ticker.get(t)
                if row and row.close and row.open:
                    has_data = True
                    chg = ((row.close - row.open) / row.open) * 100
                    direction = "gained" if chg >= 0 else "lost"
                    stock_changes.append((t, n, sector, chg, row.close))
                    articles.append({
                        "title": f"{n}{sector_tag} {direction} {abs(chg):.2f}% on {row.timestamp.date()}",
                        "summary": f"{n} closed at ₹{row.close:.2f} | High: ₹{row.high:.2f} Low: ₹{row.low:.2f} | Vol: {int(row.volume or 0):,}",
                        "sentiment": "positive" if chg >= 0 else "negative",
                        "source": "Market Data",
                        "ticker": t,
                        "url": "",
                        "published_at": str(row.timestamp.date())
                    })

        # ── Market summary from indices ──
        try:
            nifty_tick = live_ticks.get('NIFTY')
            if nifty_tick and nifty_tick.get('last_traded_price') and nifty_tick.get('change_per'):
                nifty_chg = nifty_tick['change_per']
                nifty_dir = "gained" if nifty_chg >= 0 else "declined"
                nifty_close = nifty_tick['last_traded_price']
                nifty_low = nifty_tick.get('low', nifty_close)
                nifty_high = nifty_tick.get('high', nifty_close)
                
                articles.insert(0, {
                    "title": f"Market roundup: Nifty {nifty_dir} {abs(nifty_chg):.2f}% to {nifty_close:.2f}",
                    "summary": f"Sensex {nifty_dir}. Nifty range: {nifty_low:.2f} - {nifty_high:.2f}. Banking, IT, and Auto among key movers.",
                    "sentiment": "positive" if nifty_chg >= 0 else "negative",
                    "source": "Live Market Summary",
                    "ticker": "NIFTY",
                    "url": "",
                    "published_at": today_date
                })
            else:
                def fetch_indices():
                    indices = ['NIFTY', 'SENSEX', 'BANKNIFTY']
                    rows = db.query(models.Candle).filter(
                        models.Candle.ticker.in_(indices),
                        models.Candle.timeframe == '1D',
                    ).distinct(models.Candle.ticker).order_by(
                        models.Candle.ticker, models.Candle.timestamp.desc()
                    ).all()
                    return {r.ticker: r for r in rows}

                idx_map = fetch_indices()
                nifty = idx_map.get('NIFTY')
                if nifty and nifty.close and nifty.open:
                    nifty_chg = ((nifty.close - nifty.open) / nifty.open) * 100
                    nifty_dir = "gained" if nifty_chg >= 0 else "declined"
                    articles.insert(0, {
                        "title": f"Market roundup: Nifty {nifty_dir} {abs(nifty_chg):.2f}% to {nifty.close:.2f}",
                        "summary": f"Sensex {nifty_dir}. Nifty range: {nifty.low:.2f} - {nifty.high:.2f}. Banking, IT, and Auto among key movers.",
                        "sentiment": "positive" if nifty_chg >= 0 else "negative",
                        "source": "Market Summary",
                        "ticker": "NIFTY",
                        "url": "",
                        "published_at": str(nifty.timestamp.date())
                    })
        except Exception as e:
            print(f"[News] Market summary error: {e}")

        # Top gainer / top loser articles
        try:
            gainers = sorted([sc for sc in stock_changes if sc[3] > 0], key=lambda x: -x[3])
            losers = sorted([sc for sc in stock_changes if sc[3] < 0], key=lambda x: x[3])
            if gainers:
                g = gainers[0]
                articles.insert(1, {
                    "title": f"Top gainer: {g[1]} surges {g[3]:.2f}%{f' ({g[2]})' if g[2] else ''}",
                    "summary": f"{g[1]} was the top gainer among major stocks, trading at ₹{g[4]:.2f}.",
                    "sentiment": "positive",
                    "source": "Market Movers",
                    "ticker": g[0],
                    "url": "",
                    "published_at": today_date
                })
            if losers:
                l = losers[0]
                articles.insert(2, {
                    "title": f"Top loser: {l[1]} drops {abs(l[3]):.2f}%{f' ({l[2]})' if l[2] else ''}",
                    "summary": f"{l[1]} was the top loser among major stocks, trading at ₹{l[4]:.2f}.",
                    "sentiment": "negative",
                    "source": "Market Movers",
                    "ticker": l[0],
                    "url": "",
                    "published_at": today_date
                })
                
            # Sector performance article
            sector_changes = {}
            for t, n, sec, chg, cp in stock_changes:
                if not sec: continue
                if sec not in sector_changes:
                    sector_changes[sec] = []
                sector_changes[sec].append(chg)
            if sector_changes:
                sec_avg = {s: sum(v)/len(v) for s, v in sector_changes.items()}
                best_sec = max(sec_avg, key=sec_avg.get)
                worst_sec = min(sec_avg, key=sec_avg.get)
                if best_sec == worst_sec:
                    best_sec = list(sec_avg.keys())[0]
                articles.insert(3, {
                    "title": f"{best_sec} leads, {worst_sec} lags — sector performance recap",
                    "summary": f"{best_sec} sector avg: {sec_avg[best_sec]:+.2f}% | {worst_sec} sector avg: {sec_avg[worst_sec]:+.2f}%. Track sector leaders for individual stock moves.",
                    "sentiment": "positive" if sec_avg.get('NIFTY', 0) >= 0 else "negative",
                    "source": "Sector Analysis",
                    "ticker": "",
                    "url": "",
                    "published_at": today_date
                })
        except Exception as e:
            print(f"[News] Gainer/Loser/Sector error: {e}")

        # ── Fallback to dummy data when no data ──
        # ── Fallback to dummy data when no data ──
        # ── Fallback to live prices when StockData is empty ──
        if not has_data:
            try:
                import asyncio
                # Fallback to only top 5 tickers to save time, with 10s timeout
                live = await asyncio.wait_for(fetch_batch_live_data(ticker_list[:5]), timeout=10.0)
                live_items = []
                for t in ticker_list[:5]:
                    p = live.get(t, {})
                    cp = _safe_float(p.get('current_price') or p.get('current'))
                    op = _safe_float(p.get('prev_close') or p.get('open'))
                    meta = STOCK_META.get(t, {})
                    name = meta.get('name', t)
                    sector = _SECTOR_MAP.get(t, "")
                    sector_tag = f" ({sector})" if sector else ""
                    if cp and op and op > 0:
                        chg = ((cp - op) / op) * 100
                        direction = "gained" if chg >= 0 else "lost"
                        live_items.append((t, name, sector, chg))
                        articles.append({
                            "title": f"{name}{sector_tag} {direction} {abs(chg):.2f}% in latest trade",
                            "summary": f"{name} trading at ₹{cp:.2f} (prev close ₹{op:.2f}, change {chg:+.2f}%).",
                            "sentiment": "positive" if chg >= 0 else "negative",
                            "source": "Live Market",
                            "ticker": t,
                            "url": "",
                            "published_at": (datetime.now(IST) - timedelta(minutes=len(articles) * 30)).isoformat()
                        })
                if live_items:
                    l_gainers = sorted([x for x in live_items if x[3] > 0], key=lambda x: -x[3])
                    l_losers = sorted([x for x in live_items if x[3] < 0], key=lambda x: x[3])
                    if l_gainers:
                        g = l_gainers[0]
                        articles.insert(0, {
                            "title": f"Live: {g[1]} leads with {g[3]:.2f}% gain{g[2] and f' ({g[2]})' or ''}",
                            "summary": f"{g[1]} is the top gainer in real-time trading.",
                            "sentiment": "positive",
                            "source": "Live Market",
                            "ticker": g[0],
                            "url": "",
                            "published_at": datetime.now(IST).isoformat()
                        })
                    if l_losers:
                        l = l_losers[0]
                        articles.insert(1, {
                            "title": f"Live: {l[1]} drops {abs(l[3]):.2f}%{l[2] and f' ({l[2]})' or ''}",
                            "summary": f"{l[1]} is the top loser in real-time trading.",
                            "sentiment": "negative",
                            "source": "Live Market",
                            "ticker": l[0],
                            "url": "",
                            "published_at": datetime.now(IST).isoformat()
                        })
            except Exception:
                pass

        result = {"articles": articles[:20]}
        if result["articles"]:
            _news_general_cache = result
            _news_general_cache_ts = now_ts
        return result
    except Exception as e:
        print(f"[News] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"articles": [], "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/news/ticker/{ticker}")
async def proxy_news_ticker(ticker: str, db: Session = Depends(get_db)):
    global _news_ticker_cache
    now = time.time()
    t = ticker.strip().upper().replace('.NS', '')
    cached = _news_ticker_cache.get(t)
    if cached and (now - cached["ts"]) < _NEWS_CACHE_TTL:
        return cached["data"]
    try:
        meta = db.query(models.StockMetadata).filter(models.StockMetadata.ticker == t).first()
        name = meta.name if meta else t
        
        with angelone_service.latest_ticks_lock:
            live_ticks = dict(angelone_service.latest_ticks)
            
        today_date = datetime.now(IST).strftime("%Y-%m-%d")
        
        tick = live_ticks.get(t)
        articles = []
        if tick and tick.get('last_traded_price') and tick.get('change_per'):
            chg = tick['change_per']
            cp = tick['last_traded_price']
            high = tick.get('high', cp)
            low = tick.get('low', cp)
            vol = tick.get('volume_trade_for_the_day', 0)
            
            direction = "gained" if chg >= 0 else "lost"
            articles.append({
                "title": f"{name} {direction} {abs(chg):.2f}% to ₹{cp:.2f}",
                "summary": f"{name} traded between ₹{low:.2f} and ₹{high:.2f}, closing at ₹{cp:.2f}.",
                "sentiment": "positive" if chg >= 0 else "negative",
                "source": "Live Market Data",
                "url": "",
                "published_at": today_date
            })
        else:
            row = db.query(models.Candle).filter(
                models.Candle.ticker == t,
                models.Candle.timeframe == '1D',
            ).order_by(models.Candle.timestamp.desc()).first()
            if row and row.close and row.open:
                chg = ((row.close - row.open) / row.open) * 100
                direction = "gained" if chg >= 0 else "lost"
                articles.append({
                    "title": f"{name} {direction} {abs(chg):.2f}% to ₹{row.close:.2f}",
                    "summary": f"{name} traded between ₹{row.low:.2f} and ₹{row.high:.2f}, closing at ₹{row.close:.2f}.",
                    "sentiment": "positive" if chg >= 0 else "negative",
                    "source": "Market Data",
                    "url": "",
                    "published_at": str(row.date)
                })
                
        result = {"articles": articles}
        _news_ticker_cache[t] = {"data": result, "ts": now}
        return result
    except Exception as e:
        print(f"[News Ticker] Error: {e}")
        return {"articles": []}

# ==================== DASHBOARD WEBSOCKET ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_topics: Dict[str, set] = {}

    async def connect(self, websocket: WebSocket, client_id: str = None):
        await websocket.accept()
        if client_id is None:
            client_id = str(id(websocket))
        self.active_connections[client_id] = websocket
        self.user_topics[client_id] = set()
        return client_id

    def disconnect(self, client_id: str):
        ws = self.active_connections.pop(client_id, None)
        if ws:
            try:
                import asyncio
                asyncio.create_task(ws.close())
            except Exception:
                pass
        self.user_topics.pop(client_id, None)

    async def send_personal_message(self, message: dict, client_id: str):
        ws = self.active_connections.get(client_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"[WS Dashboard] Send error: {e}")
                self.disconnect(client_id)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
            
        async def _send(cid, ws):
            import asyncio
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=0.5)
            except asyncio.TimeoutError:
                return None  # slow client, skip without disconnecting
            except Exception as e:
                print(f"[WS Dashboard] Broadcast error to {cid}: {e}")
                return cid
        
        import asyncio
        tasks = [_send(cid, ws) for cid, ws in self.active_connections.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

manager = ConnectionManager()

DASHBOARD_TICKERS = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL']
SECTOR_TICKERS = ['NIFTY_AUTO', 'NIFTY_IT', 'NIFTY_PHARMA', 'NIFTY_FMCG', 'NIFTY_METAL', 'NIFTY_ENERGY', 'NIFTY_MEDIA', 'NIFTY_PSU_BANK', 'NIFTY_REALTY']

@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    client_id = await manager.connect(websocket)
    # Send connected message so frontend subscribes
    await websocket.send_json({"type": "connected", "client_id": client_id})
    _msg_window_start = time.time()
    _msg_count = 0
    try:
        while True:
            data = await websocket.receive_json()
            # RT-03: per-connection inbound message cap. Ticker validation
            # below already blocks the garbage-string cap-exhaustion vector;
            # this bounds how fast any one connection can send messages at
            # all, since receive_json() itself has no rate limit.
            now = time.time()
            if now - _msg_window_start >= 1.0:
                _msg_window_start = now
                _msg_count = 0
            _msg_count += 1
            if _msg_count > 20:
                continue
            msg_type = data.get("type", "")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "subscribe":
                topics = data.get("topics", [])
                # Validate against the known instrument set before counting anything
                # against the subscription cap -- an unauthenticated client should
                # not be able to exhaust it with garbage ticker strings.
                valid_topics = [t for t in topics if angelone_service.get_token(t, "NSE") or angelone_service.get_token(t, "BSE")]
                # Only count tickers this client isn't already tracking -- the
                # frontend resends its whole running ticker list on every
                # subscribe call (not just the delta), so without this diff
                # every already-tracked ticker's view count would be
                # re-incremented on every chart open/switch and never fully
                # unwind on disconnect (unview_all only decrements once).
                already_tracked = manager.user_topics.get(client_id, set())
                new_topics = [t for t in valid_topics if t not in already_tracked]
                if client_id in manager.user_topics:
                    manager.user_topics[client_id].update(valid_topics)
                if viewed_ticker_mgr:
                    for t in new_topics:
                        viewed_ticker_mgr.view(t)
            elif msg_type == "unsubscribe":
                topics = data.get("topics", [])
                if client_id in manager.user_topics:
                    for t in topics:
                        manager.user_topics[client_id].discard(t)
                if viewed_ticker_mgr:
                    for t in topics:
                        viewed_ticker_mgr.unview(t)
            elif msg_type == "view_ticker":
                tkr = data.get("ticker", "").strip().upper()
                if tkr and viewed_ticker_mgr and (angelone_service.get_token(tkr, "NSE") or angelone_service.get_token(tkr, "BSE")):
                    viewed_ticker_mgr.view(tkr)
            elif msg_type == "unview_ticker":
                tkr = data.get("ticker", "").strip().upper()
                if tkr and viewed_ticker_mgr:
                    viewed_ticker_mgr.unview(tkr)
    except WebSocketDisconnect:
        if viewed_ticker_mgr and client_id in manager.user_topics:
            viewed_ticker_mgr.unview_all(list(manager.user_topics[client_id]))
        manager.disconnect(client_id)
    except Exception as e:
        print(f"[WS Dashboard] Error: {e}")
        if viewed_ticker_mgr and client_id in manager.user_topics:
            viewed_ticker_mgr.unview_all(list(manager.user_topics[client_id]))
        manager.disconnect(client_id)

# ── Background dashboard broadcast loop ──
_live_dashboard_cache = {}
_live_dashboard_cache_lock = asyncio.Lock()
_last_movers_broadcast = 0.0

async def _broadcast_dashboard():
    """Periodically fetches live prices & market movers and pushes to dashboard WS.
    
    Fast price updates come from AngelOne WS via `_broadcast_angel_ticks` (200ms).
    This loop only handles market status + movers + yfinance fallback at a reduced rate.
    """
    global _last_movers_broadcast
    while True:
        try:
            now = datetime.now(IST)
            now_ts = time.time()
            market_open = is_market_open_now()
            interval = 10.0 if market_open else 60.0

            # 1. Fetch live prices for dashboard + sector tickers not covered by AngelOne WS
            #    (AngelOne-subscribed tickers are already broadcast by _broadcast_angel_ticks)
            now_ts_float = time.time()
            with angelone_service.latest_ticks_lock:
                angel_tickers_raw = angelone_service.latest_ticks
                # Filter out stale AngelOne ticks (>30s without update = likely WS disconnected)
                angel_tickers = set()
                for at, at_data in angel_tickers_raw.items():
                    at_ts = at_data.get("_ts", 0) if isinstance(at_data, dict) else 0
                    if now_ts_float - at_ts < 45:
                        angel_tickers.add(at)
                if not angel_tickers:
                    # If ALL AngelOne ticks are stale, fall back to yfinance for everything
                    angel_tickers = set()
            # Collect all watched tickers across all connected clients (for watchlist prices)
            all_watched = set()
            for cid, topics in list(manager.user_topics.items()):
                all_watched.update(topics)
            yf_tickers = [t for t in list(dict.fromkeys(DASHBOARD_TICKERS + SECTOR_TICKERS + list(all_watched))) if t not in angel_tickers]
            if yf_tickers:
                prices = await fetch_batch_live_data(yf_tickers, market_open=market_open)
                if prices:
                    async with _live_dashboard_cache_lock:
                        _live_dashboard_cache.update(prices)
                    await manager.broadcast({
                        "type": "price_update",
                        "data": prices,
                        "ts": now.isoformat()
                    })

            # 2. Market status
            await manager.broadcast({
                "type": "market_status",
                "status": "open" if market_open else "closed",
                "ts": now.isoformat()
            })

            # 3. Market movers (throttled to 30s max)
            if market_open and (now_ts - _last_movers_broadcast >= 30):
                _last_movers_broadcast = now_ts
                try:
                    movers = await get_market_movers()
                    if isinstance(movers, dict):
                        await manager.broadcast({
                            "type": "market_movers",
                            "data": movers,
                            "ts": now.isoformat()
                        })
                except Exception as me:
                    print(f"[WS Dashboard] Movers error: {me}")

        except Exception as e:
            print(f"[WS Dashboard] Broadcast loop error: {e}")

        await asyncio.sleep(interval)

# ==================== WATCHLIST ENDPOINTS ====================

@app.get("/api/watchlist", response_model=schemas.WatchlistResponse)
async def get_watchlist(db: Session = Depends(get_db), current_user: Optional[models.User] = Depends(auth.get_current_user_optional)):
    if current_user is None:
        return schemas.WatchlistResponse(watchlist=[], count=0)
    items = db.query(models.UserWatchlist).filter(
        models.UserWatchlist.user_id == current_user.user_id
    # BUG-07 FIX: Column is `added_at`, not `created_at` — ordering by a non-existent column crashed this endpoint
    # Ordered newest-first so dashboard shows most recently added stocks
    ).order_by(models.UserWatchlist.added_at.desc()).all()
    tickers = [item.ticker for item in items]
    wl = [schemas.WatchlistItem(ticker=t, logo=_safe_logo(STOCK_META.get(t, {}).get("logo", ""))) for t in tickers]
    return schemas.WatchlistResponse(watchlist=wl, count=len(wl))

@app.post("/api/watchlist/add", response_model=schemas.WatchlistAddResponse, dependencies=[Depends(validate_csrf)])
def add_to_watchlist(
    ticker: str = Query(..., description="Ticker symbol to add"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    ticker = ticker.strip().upper()
    existing = db.query(models.UserWatchlist).filter(
        models.UserWatchlist.user_id == current_user.user_id,
        models.UserWatchlist.ticker == ticker
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"'{ticker}' already in watchlist")
    item = models.UserWatchlist(user_id=current_user.user_id, ticker=ticker)
    db.add(item)
    db.commit()
    return schemas.WatchlistAddResponse(message=f"{ticker} added to watchlist", ticker=ticker)

# ==================== FII/DII DATA ====================

_fii_dii_cache = {"data": None, "ts": 0.0}


def _fii_dii_persist(entries, source):
    """Upsert scraped sessions so a later failed scrape can still serve real numbers."""
    if not entries:
        return
    try:
        from database import SessionLocal as _S
        s = _S()
        try:
            for e in entries:
                d = str(e.get("date") or "").strip()
                if not d:
                    continue
                row = s.query(models.FiiDiiFlow).filter(models.FiiDiiFlow.date == d).first()
                if row is None:
                    row = models.FiiDiiFlow(date=d)
                    s.add(row)
                row.fii_cash_cr = _safe_float(e.get("fii_cash_cr"))
                row.dii_cash_cr = _safe_float(e.get("dii_cash_cr"))
                row.fii_fo_cr = _safe_float(e.get("fii_fo_cr"))
                row.net_total_cr = _safe_float(e.get("net_total_cr"))
                row.source = source
            s.commit()
        finally:
            s.close()
    except Exception as e:
        print(f"[FII/DII] persist failed: {e}")


def _fii_dii_from_db(limit=10):
    """Last known-good sessions, newest first."""
    try:
        from database import SessionLocal as _S
        s = _S()
        try:
            rows = (s.query(models.FiiDiiFlow)
                      .order_by(models.FiiDiiFlow.date.desc())
                      .limit(limit).all())
            return [{
                "date": r.date,
                "fii_cash_cr": r.fii_cash_cr,
                "dii_cash_cr": r.dii_cash_cr,
                "fii_fo_cr": r.fii_fo_cr,
                "net_total_cr": r.net_total_cr,
            } for r in rows]
        finally:
            s.close()
    except Exception as e:
        print(f"[FII/DII] DB read failed: {e}")
        return []


@app.get("/api/fii-dii")
async def get_fii_dii():
    """Fetch FII/DII data: tries NSE with session cookie first, then Moneycontrol scrape."""
    global _fii_dii_cache
    now = time.time()
    
    if _fii_dii_cache["data"] is not None and now - _fii_dii_cache["ts"] < 3600:
        return _fii_dii_cache["data"]
        
    today_str = datetime.now(IST).strftime("%d-%m-%Y")

    # ── Attempt 1: NSE India (requires session cookie from homepage) ──
    try:
        # 30s, not 10s: this app does blocking yfinance work on the event loop, which
        # starves in-flight async HTTP and made both scrapes time out (they surface as
        # an empty exception message) even while the upstream sites were healthy.
        async with httpx.AsyncClient(timeout=30) as client:
            client.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            await client.get("https://www.nseindia.com")
            await asyncio.sleep(1)
            url = f"https://www.nseindia.com/api/fiidii?from={today_str}&to={today_str}"
            resp = await client.get(url, headers={"Accept": "application/json, text/javascript, */*; q=0.01", "Referer": "https://www.nseindia.com/"})
            if resp.status_code == 200:
                data = resp.json()
                raw_list = data if isinstance(data, list) else data.get("data", [])
                entries = []
                for item in raw_list:
                    def _parse(val):
                        try: return float(str(val).replace(',', ''))
                        except Exception: return 0.0
                    fii_cash = _parse(item.get("FII Cash", item.get("fiiCash", item.get("fii_cash", 0))))
                    dii_cash = _parse(item.get("DII Cash", item.get("diiCash", item.get("dii_cash", 0))))
                    fii_fo   = _parse(item.get("FII FO",   item.get("fiiFo",   item.get("fii_fo",   0))))
                    net      = _parse(item.get("Net Total", item.get("netTotal", item.get("net_total", fii_cash + dii_cash))))
                    entries.append({
                        "date": item.get("date", today_str),
                        "fii_cash_cr": fii_cash,
                        "dii_cash_cr": dii_cash,
                        "fii_fo_cr": fii_fo,
                        "net_total_cr": net,
                    })
                if entries:
                    _fii_dii_persist(entries, "NSE India")
                    res = {"entries": entries, "source": "NSE India", "last_updated": datetime.now(IST).isoformat()}
                    _fii_dii_cache["data"] = res
                    _fii_dii_cache["ts"] = time.time()
                    return res
    except Exception as e:
        print(f"[FII/DII] NSE attempt failed: {e}")

    # ── Attempt 2: Moneycontrol FII/DII scrape ──
    try:
        mc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.moneycontrol.com/",
        }
        async with httpx.AsyncClient(headers=mc_headers, timeout=30) as client:
            mc_resp = await client.get("https://www.moneycontrol.com/markets/fii-dii-data/")
            if mc_resp.status_code == 200:
                import re, json as _json
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>({.*?})</script>', mc_resp.text, re.DOTALL)
                if m:
                    payload = _json.loads(m.group(1))
                    rows = payload.get("props", {}).get("pageProps", {}).get("FiiDiiData", {}).get("fiiDiiData", [])
                    entries = []
                    for row in rows[:5]:
                        def _mc_parse(s):
                            try: return float(str(s).replace(',', '').replace('\u20b9', '').strip())
                            except Exception: return 0.0
                        fii_fo = _mc_parse(row.get("fiiIdxFut","0")) + _mc_parse(row.get("fiiIdxOpt","0")) + _mc_parse(row.get("fiiStkFut","0")) + _mc_parse(row.get("fiiStkOpt","0"))
                        fii_cash = _mc_parse(row.get("fiiCM","0"))
                        dii_cash = _mc_parse(row.get("diiCM","0"))
                        entries.append({
                            "date": row.get("date",""),
                            "fii_cash_cr": fii_cash,
                            "dii_cash_cr": dii_cash,
                            "fii_fo_cr": fii_fo,
                            "net_total_cr": dii_cash + fii_cash,
                        })
                    if entries:
                        _fii_dii_persist(entries, "Moneycontrol")
                        res = {"entries": entries, "source": "Moneycontrol", "last_updated": datetime.now(IST).isoformat()}
                        _fii_dii_cache["data"] = res
                        _fii_dii_cache["ts"] = time.time()
                        return res
    except Exception as e:
        print(f"[FII/DII] Moneycontrol attempt failed: {e}")

    # ── Both scrapes failed: serve the last sessions we successfully stored ──
    # Previously this overwrote the cache with an empty payload, so one flaky scrape
    # replaced perfectly good figures with "N/A" until the next successful fetch.
    stored = _fii_dii_from_db()
    if stored:
        res = {
            "entries": stored,
            "source": "Saved (live feed unavailable)",
            "last_updated": datetime.now(IST).isoformat(),
            "stale": True,
        }
        _fii_dii_cache["data"] = res
        _fii_dii_cache["ts"] = time.time() - 3300  # retry upstream in ~5 min
        return res

    fallback = {"entries": [], "source": "Unavailable", "last_updated": datetime.now(IST).isoformat(), "error": "FII/DII data temporarily unavailable. NSE blocks direct API access without browser session."}
    _fii_dii_cache["data"] = fallback
    _fii_dii_cache["ts"] = time.time() - 3540 # Cache for 60 seconds (since TTL is 3600)
    return fallback

# ==================== PORTFOLIO ENDPOINTS ====================

@app.get("/api/portfolio/open-positions")
def get_open_positions(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    from database import get_ist_now
    positions = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id,
        models.Position.status == "OPEN"
    ).order_by(models.Position.created_at.desc()).all()

    # BUG-09 FIX: Fetch live prices from CurrentDayCandle (today only) instead of hardcoding entry_price
    live_prices = {}
    tickers = list(set(p.ticker for p in positions))
    if tickers:
        try:
            today = get_ist_now().date()
            intraday_candles_5min = db.query(models.CurrentDayCandle).filter(
                models.CurrentDayCandle.ticker.in_(tickers),
                models.CurrentDayCandle.trading_date == today
            ).all()
            for c in intraday_candles_5min:
                live_prices[c.ticker] = c.current_price
        except Exception as e:
            print(f"[Portfolio] Live price fetch error: {e}")

    # DB-06: one batched query for every position's TP/SL orders instead of
    # one query per position in the loop below.
    orders_by_position = TradingService.get_orders_for_positions(db, [pos.id for pos in positions])

    result = []
    for pos in positions:
        orders = orders_by_position.get(pos.id, {"TP": None, "SL": None})
        current_price = live_prices.get(pos.ticker, pos.entry_price)
        if pos.position_type == "LONG":
            unrealized_pnl = round((current_price - pos.entry_price) * pos.quantity, 2)
        else:
            unrealized_pnl = round((pos.entry_price - current_price) * pos.quantity, 2)
        result.append({
            "id": pos.id,
            "ticker": pos.ticker,
            "stock_name": pos.stock_name,
            "position_type": pos.position_type,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "current_price": current_price,
            "unrealized_pnl": unrealized_pnl,
            "total_investment": pos.total_investment,
            "created_at": pos.created_at.isoformat(),
            "tp_edit_count": pos.tp_edit_count,
            "sl_edit_count": pos.sl_edit_count,
            "take_profit": orders["TP"].trigger_price if orders.get("TP") else None,
            "stop_loss": orders["SL"].trigger_price if orders.get("SL") else None
        })
    return result

@app.get("/api/portfolio/history")
def get_portfolio_history(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """Return portfolio cash balance history from transactions."""
    txns = db.query(models.Transaction).filter(
        models.Transaction.user_id == current_user.user_id
    ).order_by(models.Transaction.created_at.asc()).all()
    history = []
    for t in txns:
        ts = t.created_at.timestamp() if hasattr(t.created_at, 'timestamp') else t.created_at
        history.append({
            "date": t.created_at.isoformat() if hasattr(t.created_at, 'isoformat') else str(t.created_at),
            "timestamp": int(ts) if isinstance(ts, (int, float)) else 0,
            "balance": round(t.balance_after, 2),
            "type": t.transaction_type,
            "ticker": t.ticker,
            "pnl": round(t.pnl, 2) if t.pnl else 0
        })
    return history

@app.get("/api/portfolio/summary")
def get_portfolio_summary(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    user = db.query(models.User).filter(models.User.user_id == current_user.user_id).first()
    balance = user.virtual_balance if user else 100000.00
    open_positions = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id, models.Position.status == "OPEN"
    ).count()
    closed_positions = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id, models.Position.status == "CLOSED"
    ).count()
    total_invested = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id, models.Position.status == "OPEN"
    ).with_entities(models.Position.total_investment).all()
    invested_sum = sum(p[0] for p in total_invested if p[0]) if total_invested else 0
    total_realized = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id, models.Position.status == "CLOSED"
    ).with_entities(models.Position.realized_pnl).all()
    realized_pnl = sum(p[0] for p in total_realized if p[0]) if total_realized else 0
    total_positions = open_positions + closed_positions

    # Win Rate calculation: PnL >= 0 counts as a Win
    pnl_rows = db.query(models.Position.realized_pnl).filter(
        models.Position.user_id == current_user.user_id,
        models.Position.status == "CLOSED",
        models.Position.realized_pnl.isnot(None)
    ).all()
    total_wins = sum(1 for r in pnl_rows if r[0] >= 0)
    total_losses = sum(1 for r in pnl_rows if r[0] < 0)
    total_closed = len(pnl_rows)
    win_rate = round((total_wins / total_closed) * 100, 1) if total_closed > 0 else 0.0

    # Compute unrealized P&L from open positions using live prices
    open_positions_rows = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id, models.Position.status == "OPEN"
    ).all()
    unrealized_pnl = 0.0
    if open_positions_rows:
        open_tickers = list(set(p.ticker for p in open_positions_rows))
        today = date.today()
        intraday_candles_5min = db.query(models.CurrentDayCandle).filter(
            models.CurrentDayCandle.ticker.in_(open_tickers),
            models.CurrentDayCandle.trading_date == today
        ).all()
        live_px = {c.ticker: c.current_price for c in intraday_candles_5min}
        for p in open_positions_rows:
            cp = live_px.get(p.ticker, p.entry_price)
            if p.position_type == "LONG":
                unrealized_pnl += (cp - p.entry_price) * p.quantity
            else:
                unrealized_pnl += (p.entry_price - cp) * p.quantity
    unrealized_pnl = round(unrealized_pnl, 2)

    return {
        "balance": round(balance, 2),
        "current_balance": round(balance, 2),
        "total_invested": round(invested_sum, 2),
        "realized_pnl": round(realized_pnl, 2),
        "total_realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": unrealized_pnl,
        "total_unrealized_pnl": unrealized_pnl,
        "total_positions": total_positions,
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "win_rate": win_rate,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "wins": total_wins,
        "losses": total_losses
    }

@app.post("/api/trade/close-position")
def close_position(req: schemas.ClosePositionRequest, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    # Validate closing price against market price BEFORE closing
    pos = db.query(models.Position).filter(
        models.Position.id == req.position_id,
        models.Position.user_id == current_user.user_id,
        models.Position.status == "OPEN"
    ).first()
    if pos:
        try:
            with angelone_service.latest_ticks_lock:
                ticker_live = angelone_service.latest_ticks.get(pos.ticker)
            if ticker_live and ticker_live.get("current_price", 0) > 0:
                market_price = ticker_live["current_price"]
                deviation = abs(req.closing_price - market_price) / market_price
                if deviation > 0.05:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Closing price ₹{req.closing_price:.2f} deviates {deviation*100:.1f}% from market price ₹{market_price:.2f}. Max allowed deviation is 5%."
                    )
        except HTTPException:
            raise
        except Exception as e:
            print(f"[ClosePosition] Price validation error: {e}")

    position, error_msg = TradingService.close_position(
        db=db, user_id=current_user.user_id,
        position_id=req.position_id,
        closing_price=req.closing_price,
        close_type="MANUAL"
    )
    if not position:
        raise HTTPException(status_code=400, detail=error_msg)
    return {"message": "Position closed", "position_id": position.id, "realized_pnl": position.realized_pnl}

@app.post("/api/trade/set-limits")
def set_limits(req: schemas.SetLimitsRequest, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    success, msg = TradingService.set_limits(
        db=db, user_id=current_user.user_id,
        position_id=req.position_id,
        take_profit=req.take_profit,
        stop_loss=req.stop_loss
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "position_id": req.position_id}

@app.get("/api/top-9-history")
def get_top_9_history(db: Session = Depends(get_db)):
    # Frontend's hardcoded TRACKER_TICKERS — return history for the actual displayed tickers
    TOP9_TICKERS = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL']
    top_tickers = db.query(models.StockMetadata.ticker, models.StockMetadata.name).filter(
        models.StockMetadata.ticker.in_(TOP9_TICKERS)
    ).all()
    ticker_list = [t for t, n in top_tickers]
    result = {}
    if not ticker_list:
        return result
    all_data = db.query(models.Candle).filter(
        models.Candle.ticker.in_(ticker_list),
        models.Candle.timeframe == '1D',
    ).order_by(models.Candle.ticker, models.Candle.timestamp.desc()).all()
    ticker_data = {}
    for r in all_data:
        if r.ticker not in ticker_data:
            ticker_data[r.ticker] = []
        if len(ticker_data[r.ticker]) < 2:
            ticker_data[r.ticker].append(r)
    name_map = dict(top_tickers)
    for t in ticker_list:
        data = ticker_data.get(t, [])
        n = name_map.get(t, t)
        if len(data) >= 2:
            change = round(data[0].close - data[1].close, 2)
            change_pct = round((change / data[1].close) * 100, 2) if data[1].close else 0
        else:
            change = 0
            change_pct = 0
        result[t] = {"name": n, "current_price": data[0].close if data else 0, "change": change, "change_pct": change_pct}
    return result

# ==================== STOCK DATA RANGE / HISTORY ====================

def _normalize_ticker(ticker: str) -> List[str]:
    t = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    variants = [t]
    if '.' not in t and t not in ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCAP', 'SMALLCAP']:
        variants.append(f"{t}.NS")
    return variants



@app.get("/api/stock-data/range")
def get_stock_data_range(ticker: str = Query(...), range: str = Query("ALL"), db: Session = Depends(get_db)):
    """Daily OHLCV range. Primary source: candles(1D). Fallback: yfinance → AngelOne (writes to candles).
    Response: list of {"time": "YYYY-MM-DD", "open", "high", "low", "close", "adj_close", "volume"}."""
    db_tickers = _normalize_ticker(ticker)
    ist_now = database.get_ist_now()
    range_days = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365}.get(range.upper())
    clean_ticker = ticker.strip().upper().replace('.NS', '').replace('.BO', '')

    def _query_candles_1d():
        q = db.query(models.Candle).filter(
            models.Candle.ticker.in_(db_tickers),
            models.Candle.timeframe == '1D',
        )
        if range_days is not None:
            cutoff = ist_now.date() - timedelta(days=range_days)
            q = q.filter(models.Candle.timestamp >= datetime(cutoff.year, cutoff.month, cutoff.day))
        return q.order_by(models.Candle.timestamp.desc()).limit(500).all()

    records = _query_candles_1d()

    resolved_exchange = _resolve_ticker_exchange(db, clean_ticker)
    MIN_DAILY_RECORDS = 10 if range.upper() == "ALL" else 5

    # Fallback to yfinance if insufficient data in candles[1D]
    if len(records) < MIN_DAILY_RECORDS and not _is_yfinance_failed(clean_ticker):
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            yf_ticker = _yfinance_ticker(clean_ticker, resolved_exchange)
            period_days = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y"}.get(range.upper(), "5y")
            yf_data, yf_err = yf_downloader.download_single(
                yf_ticker, period=period_days, interval="1d", timeout=8
            )
            if yf_err is None and yf_data is not None and not yf_data.empty:
                if isinstance(yf_data.columns, pd.MultiIndex):
                    if yf_ticker in yf_data.columns.get_level_values(1):
                        yf_data = yf_data.xs(yf_ticker, axis=1, level=1)
                    else:
                        yf_data.columns = yf_data.columns.get_level_values(0)
                try:
                    for idx, row in yf_data.iterrows():
                        try:
                            dt = idx.date() if hasattr(idx, 'date') else idx
                            ts = datetime(dt.year, dt.month, dt.day, 0, 0, 0)
                            db.execute(pg_insert(models.Candle).values(
                                ticker=clean_ticker, timeframe='1D', timestamp=ts,
                                open=_safe_float(row.get('Open')), high=_safe_float(row.get('High')),
                                low=_safe_float(row.get('Low')), close=_safe_float(row.get('Close')),
                                volume=_safe_int(row.get('Volume')),
                                is_completed=True, data_source='YFINANCE', is_backfilled=True,
                            ).on_conflict_do_nothing(constraint="uix_candle_key"))
                        except Exception:
                            pass
                    db.commit()
                    records = _query_candles_1d()
                except Exception:
                    db.rollback()
            else:
                _mark_yfinance_failed(clean_ticker)
        except Exception:
            _mark_yfinance_failed(clean_ticker)

    # Fallback to AngelOne if still insufficient (covers SME stocks yfinance doesn't have)
    if len(records) < MIN_DAILY_RECORDS:
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            if not historical_service.is_logged_in:
                historical_service.login()
            if historical_service.is_logged_in:
                from_date = ist_now.date() - timedelta(days=365*5 if range.upper() == "ALL" else range_days if range_days else 365)
                to_date = ist_now.date()
                angel_daily_candles = historical_service.get_historical_candles(
                    ticker=clean_ticker, interval='ONE_DAY',
                    from_date=from_date, to_date=to_date, exchange=resolved_exchange
                )
                if angel_daily_candles:
                    try:
                        for c in angel_daily_candles:
                            ts_raw = c.get('timestamp')
                            if not isinstance(ts_raw, datetime):
                                continue
                            ts = datetime(ts_raw.year, ts_raw.month, ts_raw.day, 0, 0, 0)
                            db.execute(pg_insert(models.Candle).values(
                                ticker=clean_ticker, timeframe='1D', timestamp=ts,
                                open=_safe_float(c.get('open')), high=_safe_float(c.get('high')),
                                low=_safe_float(c.get('low')), close=_safe_float(c.get('close')),
                                volume=_safe_int(c.get('volume')),
                                is_completed=True, data_source='ANGELONE', is_backfilled=True,
                            ).on_conflict_do_nothing(constraint="uix_candle_key"))
                        db.commit()
                        records = _query_candles_1d()
                    except Exception:
                        db.rollback()
        except Exception as e:
            print(f"[WARN] AngelOne historical fallback failed for {clean_ticker}: {e}")

    # Build response using date string format (preserves existing API contract).
    result = [
        {"time": str(r.timestamp.date()), "open": _safe_float(r.open), "high": _safe_float(r.high),
         "low": _safe_float(r.low), "close": _safe_float(r.close),
         "adj_close": _safe_float(r.close), "volume": _safe_int(r.volume)}
        for r in reversed(records)
    ]
    today = ist_now.date()
    live = db.query(models.CurrentDayCandle).filter(
        models.CurrentDayCandle.ticker.in_(db_tickers),
        models.CurrentDayCandle.trading_date == today
    ).first()
    if live:
        result = [r for r in result if r["time"] != str(live.trading_date)]
        result.append({
            "time": str(live.trading_date),
            "open": _safe_float(live.open),
            "high": _safe_float(live.high),
            "low": _safe_float(live.low),
            "close": _safe_float(live.current_price),
            "adj_close": _safe_float(live.current_price),
            "volume": _safe_int(live.volume)
        })
    return result


# In-memory cache for yfinance intraday results (no DB persistence)
# Key: (ticker, interval) -> (timestamp, list of Candle-like dicts)
_yf_intraday_cache: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}
# Per-ticker cooldown: minimum seconds between yfinance fetches for same ticker
_yf_last_fetch: Dict[str, float] = {}
_YF_COOLDOWN = 60  # 60 seconds per ticker

def _get_yf_cache_ttl() -> int:
    """Tiered TTL: 5 min during market hours, 1h off-hours, 6h weekends."""
    now = database.get_ist_now()
    if now.weekday() >= 5:
        return 21600  # 6 hours on weekends
    t = now.time()
    if t >= now.replace(hour=9, minute=15).time() and t <= now.replace(hour=15, minute=30).time():
        return 300    # 5 minutes during market
    return 3600       # 1 hour after market

class _YfCandle:
    """Simple object mimicking Candle model attributes for intraday data."""
    __slots__ = ('ticker', 'timeframe', 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'is_completed')
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

def _fetch_yfinance_intraday(db, clean_ticker: str, interval: str = "5m", use_bg_semaphore: bool = False):
    """Fetch intraday data from yfinance. No DB persistence — returned in-memory only.
    Uses centralized YFinanceDownloader for consistent error handling and retry.
    Tiered TTL: 5m market hours, 1h off-hours, 6h weekends.
    60s per-ticker cooldown to prevent hammering yfinance."""
    if _is_yfinance_failed(clean_ticker):
        return []
    # Validation removed to allow dynamically fetching valid symbols missing from stocks_temp.json
    now_ts = time.time()
    cache_key = (clean_ticker, interval)
    if cache_key in _yf_intraday_cache:
        cached_at, cached_data = _yf_intraday_cache[cache_key]
        if now_ts - cached_at < _get_yf_cache_ttl():
            _monitoring["yfinance_cache_hits"] += 1
            return cached_data
    _monitoring["yfinance_cache_misses"] += 1

    # Per-ticker cooldown: skip if fetched within last 60s
    last_fetch = _yf_last_fetch.get(clean_ticker, 0)
    if now_ts - last_fetch < _YF_COOLDOWN:
        if cache_key in _yf_intraday_cache:
            return _yf_intraday_cache[cache_key][1]
        return []

    from aggregator import snap_to_nse_session, fix_ohlc

    yf_ticker = _yfinance_ticker(clean_ticker)
    period_map = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d"}
    yf_period = period_map.get(interval, "60d")

    # Acquire semaphore (separate pool for background tasks)
    sem = _yf_bg_semaphore if use_bg_semaphore else _yf_semaphore
    _monitoring["yfinance_semaphore_total_waits"] += 1
    acquired = sem.acquire(blocking=True, timeout=30)
    if not acquired:
        _monitoring["yfinance_semaphore_timeouts"] += 1
        print(f"[YFRateLimit] Timeout fetching {yf_ticker} — returning stale data")
        if cache_key in _yf_intraday_cache:
            return _yf_intraday_cache[cache_key][1]
        return []
    try:
        # Increased timeout 3s→10s: a 3s timeout caused valid stocks to be blacklisted
        # on any day Yahoo Finance was slightly slow, showing "no data" for 2-10 minutes.
        df, error = yf_downloader.download_single(
            yf_ticker, period=yf_period, interval=interval, timeout=10
        )
    finally:
        sem.release()
    _yf_last_fetch[clean_ticker] = time.time()

    if error or df is None or df.empty:
        _monitoring["yfinance_errors"] += 1
        _mark_yfinance_failed(clean_ticker)
        return _yf_intraday_cache.get(cache_key, ([],))[0]
    _monitoring["yfinance_requests"] += 1

    yf_int = df

    # ── Flatten MultiIndex columns ──
    if isinstance(yf_int.columns, pd.MultiIndex):
        if yf_ticker in yf_int.columns.get_level_values(1):
            yf_int = yf_int.xs(yf_ticker, axis=1, level=1)
        else:
            yf_int.columns = yf_int.columns.get_level_values(0)

    # Snap yfinance timestamps to NSE session-aligned buckets
    bucket_min = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(interval, 5)
    intraday_candles_5min = []
    seen_ts = set()
    for idx, row in yf_int.iterrows():
        try:
            ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if hasattr(idx, 'tz') and idx.tz is not None:
                ts = ts.astimezone(IST).replace(tzinfo=None)
            else:
                ts = ts.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)
            epoch = int(ts.replace(tzinfo=IST).timestamp())
            snapped_epoch = snap_to_nse_session(epoch, bucket_min)
            snapped_dt = _epoch_to_ist_dt(snapped_epoch)
            o = _safe_float(row.get('Open'))
            h = _safe_float(row.get('High'))
            l = _safe_float(row.get('Low'))
            c = _safe_float(row.get('Close'))
            v = _safe_int(row.get('Volume'))
            if o is None and h is None and l is None and c is None:
                continue
            o = o or 0.0; h = h or 0.0; l = l or 0.0; c = c or 0.0
            o, h, l, c = fix_ohlc(o, h, l, c)
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                continue
            # Deduplicate by snapped timestamp
            ts_key = snapped_dt.strftime("%Y%m%d%H%M")
            if ts_key in seen_ts:
                continue
            seen_ts.add(ts_key)
            intraday_candles_5min.append(_YfCandle(
                ticker=clean_ticker, timeframe=interval, timestamp=snapped_dt,
                open=o, high=h, low=l, close=c, volume=v, is_completed=True,
            ))
        except Exception:
            pass

    if intraday_candles_5min:
        _yf_intraday_cache[cache_key] = (time.time(), intraday_candles_5min)

    return intraday_candles_5min



# ── Use unified Candle model for all intraday queries ──────────────────
from aggregator import (
    snap_to_nse_session, is_trading_day, is_market_hour,
    ist_now_naive, fix_ohlc, validate_ohlc, candle_aggregator,
)
from event_bus import event_bus
from exchange_calendar import nse_calendar
from recovery_service import RecoveryService
from live_timeframe_manager import live_timeframe_manager
from candle_cache import candle_cache
from chart_service import ChartService
from resampler import CandleResampler
from retention_service import RetentionService
from monitor import monitor
from validation_service import ValidationService


def _intraday_cutoff(interval: str):
    """Return IST-naive datetime cutoff for intraday DB queries.
    Uses generous windows so weekends/holidays (where last session may be
    2-4+ days ago) never produce an empty result.
    """
    ist_now = database.get_ist_now()
    # 1m: 7 days covers a full trading week + weekend gap
    # 5m: 65 days covers 2 months of data on first load
    # 15m/30m: 45/90 days, 1h: 120 days
    days_back = {"1m": 7, "5m": 65, "15m": 45, "30m": 90, "1h": 120}.get(interval, 10)
    return ist_now - timedelta(days=days_back)


@app.get("/api/stock-data/intraday")
def get_stock_data_intraday(ticker: str = Query(...), interval: str = Query("5m"), after: int = Query(None), db: Session = Depends(get_db)):
    """Legacy intraday endpoint redirected to ChartService."""
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    if _chart_service is None:
        return []
    
    start_ts = after
    if start_ts is None:
        start_ts = _ts_to_epoch(_intraday_cutoff(interval))
        
    # Pass 'after' as 'start' timestamp
    result = _chart_service.get_chart(clean, interval, start=start_ts, limit=5000)
    
    # The frontend expects an array of candle objects for this endpoint
    if isinstance(result, dict) and "candles" in result:
        return result["candles"]
    elif isinstance(result, list):
        return result
    return []


def _resample_gap_fill(clean: str, target_tf: str, stored_candles: list, db_tickers: list, db) -> list:
    """
    Build 1W/1M candles from ALL available 1D data (DB + yfinance gap-fill) then merge with
    any stored 1W/1M candles, keeping stored bars where they already exist.

    This gives maximum historical coverage: if the DB holds 10 years of daily data,
    the chart shows 10 years of weekly/monthly bars even when stored_candles only goes back 4 years.

    Returns the merged + sorted candle list. Never writes to the DB — read-only path.
    """
    from resampler import CandleResampler
    import pandas as pd

    today_ist = get_ist_now().date()

    # Always fetch ALL 1D candles from DB — not just the gap — so we can resample the full history.
    db_1d = db.query(models.Candle).filter(
        models.Candle.ticker.in_(db_tickers),
        models.Candle.timeframe == "1D",
    ).order_by(models.Candle.timestamp.asc()).all()

    daily_dicts = []
    for r in db_1d:
        ts = r.timestamp if isinstance(r.timestamp, datetime) else datetime.combine(r.timestamp, datetime.min.time())
        daily_dicts.append({"timestamp": ts, "open": _safe_float(r.open), "high": _safe_float(r.high),
                             "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_int(r.volume)})

    # Check if the 1D data covers up to ~today; fetch from yfinance only if there is a recent gap.
    db_covers_to = daily_dicts[-1]["timestamp"].date() if daily_dicts else None
    need_yf = db_covers_to is None or (today_ist - db_covers_to).days > 7

    if need_yf:
        try:
            yf_sym = _yfinance_ticker(clean)
            # Fetch from a week before the last 1D in DB (or max history if no 1D at all)
            fetch_from = (db_covers_to - timedelta(days=7)).strftime('%Y-%m-%d') if db_covers_to else None
            acquired = _yf_semaphore.acquire(blocking=True, timeout=20)
            if acquired:
                try:
                    if fetch_from:
                        df = yf.download(yf_sym, start=fetch_from, interval='1d',
                                         progress=False, auto_adjust=True, timeout=15)
                    else:
                        df = yf.download(yf_sym, period='max', interval='1d',
                                         progress=False, auto_adjust=True, timeout=15)
                finally:
                    _yf_semaphore.release()

                if df is not None and not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    yf_seen = {d["timestamp"].date() for d in daily_dicts}
                    for idx, row in df.iterrows():
                        ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                        ts = ts.replace(tzinfo=None) if getattr(ts, 'tzinfo', None) else ts
                        o = float(row.get('Open', 0) or 0)
                        h = float(row.get('High', 0) or 0)
                        l = float(row.get('Low', 0) or 0)
                        c = float(row.get('Close', 0) or 0)
                        v = int(row.get('Volume', 0) or 0)
                        if o > 0 and c > 0 and ts.date() not in yf_seen:
                            daily_dicts.append({"timestamp": ts, "open": o, "high": max(o, h, c),
                                                "low": min(o, l, c), "close": c, "volume": v})
                            yf_seen.add(ts.date())
        except Exception as e:
            print(f"[GapFill] yfinance error for {clean}: {e}")

    if not daily_dicts:
        return stored_candles

    daily_dicts.sort(key=lambda d: d["timestamp"])

    try:
        resampled = CandleResampler.resample_5m_to(daily_dicts, target_tf)
    except Exception as e:
        print(f"[GapFill] Resample error {clean} {target_tf}: {e}")
        return stored_candles

    stored_times = {c["time"] for c in stored_candles}
    added = 0
    for c in resampled:
        ts = c.get("timestamp")
        if isinstance(ts, datetime):
            # Match _ts_to_epoch: treat naive datetime as IST
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            ts_epoch = int(ts.replace(tzinfo=IST).timestamp())
        else:
            ts_epoch = int(ts) if ts else 0
        if ts_epoch > 0 and ts_epoch not in stored_times:
            stored_candles.append({
                "time": ts_epoch,
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": int(c.get("volume", 0)),
            })
            stored_times.add(ts_epoch)
            added += 1

    if added:
        stored_candles.sort(key=lambda c: c["time"])
        print(f"[GapFill] {clean} {target_tf}: +{added} resampled candles from full 1D history")

    return stored_candles


@app.get("/api/stock-data/weekly")
def get_weekly_intraday_candles_5min(ticker: str = Query(...), db: Session = Depends(get_db)):
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    db_tickers = _normalize_ticker(ticker)
    # 1. Stored 1W candles (the historical base)
    stored_rows = db.query(models.Candle).filter(
        models.Candle.ticker.in_(db_tickers),
        models.Candle.timeframe == "1W",
    ).order_by(models.Candle.timestamp.asc()).all()
    stored = [{"time": _ts_to_epoch(r.timestamp), "open": _safe_float(r.open),
               "high": _safe_float(r.high), "low": _safe_float(r.low),
               "close": _safe_float(r.close), "volume": _safe_int(r.volume)} for r in stored_rows]
    # 2. Fill gap to today via 1D resample (uses DB 1D first, then yfinance for remainder)
    return _resample_gap_fill(clean, "1W", stored, db_tickers, db)


@app.get("/api/stock-data/monthly")
def get_monthly_intraday_candles_5min(ticker: str = Query(...), db: Session = Depends(get_db)):
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    db_tickers = _normalize_ticker(ticker)
    # 1. Stored 1M candles (the historical base)
    stored_rows = db.query(models.Candle).filter(
        models.Candle.ticker.in_(db_tickers),
        models.Candle.timeframe == "1M",
    ).order_by(models.Candle.timestamp.asc()).all()
    stored = [{"time": _ts_to_epoch(r.timestamp), "open": _safe_float(r.open),
               "high": _safe_float(r.high), "low": _safe_float(r.low),
               "close": _safe_float(r.close), "volume": _safe_int(r.volume)} for r in stored_rows]
    # 2. Fill gap to today via 1D resample (uses DB 1D first, then yfinance for remainder)
    return _resample_gap_fill(clean, "1M", stored, db_tickers, db)


# Candle save endpoint removed — aggregator writes intraday_candles_5min directly via batch_flush_intraday_candles_5min.
# Keeping any user-writable candle endpoint is a security risk (fake intraday_candles_5min on all charts).

# ==================== NEW CHART SERVICE ENDPOINT ====================

@app.get("/api/stock-data/chart")
def get_chart_service(ticker: str = Query(...), timeframe: str = Query("5m"),
                      start: int = Query(None), end: int = Query(None),
                      limit: int = Query(5000, ge=1, le=50000)):
    """Seamless chart endpoint using ChartService.
    Merges live + cached + stored + resampled data into one continuous response."""
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    if _chart_service is None:
        return {"intraday_candles_5min": [], "metadata": {"error": "ChartService not initialized"}}
    return _chart_service.get_chart(clean, timeframe, start, end, limit)


# ==================== PAGINATED / CACHE-OPTIMIZED ENDPOINTS ====================

@app.get("/api/stock-data/candle/latest")
def get_latest_candle(ticker: str = Query(...), interval: str = Query("5m"), db: Session = Depends(get_db)):
    """Return the latest completed candle for WS reconnect candle recovery."""
    db_tickers = _normalize_ticker(ticker)
    
    if interval in ["1m", "5m", "15m", "30m", "1h"]:
        record = db.query(models.Candle).filter(
            models.Candle.ticker.in_(db_tickers),
            models.Candle.timeframe == interval
        ).order_by(models.Candle.timestamp.desc()).first()
        if record:
            return {"time": _ts_to_epoch(record.timestamp), "open": _safe_float(record.open), "high": _safe_float(record.high), "low": _safe_float(record.low), "close": _safe_float(record.close), "volume": _safe_int(record.volume)}
    elif interval in ["1d", "1D"]:
        record = db.query(models.Candle).filter(
            models.Candle.ticker.in_(db_tickers),
            models.Candle.timeframe == '1D',
        ).order_by(models.Candle.timestamp.desc()).first()
        if record:
            return {"time": _ts_to_epoch(record.timestamp), "open": _safe_float(record.open), "high": _safe_float(record.high), "low": _safe_float(record.low), "close": _safe_float(record.close), "volume": _safe_int(record.volume)}
    return {}


@app.get("/api/stock-data/intraday/paginated")
def get_intraday_paginated(ticker: str = Query(...), interval: str = Query("5m"), before: int = Query(None), after: int = Query(None), limit: int = Query(1500, ge=1, le=50000), background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    """Paginated intraday intraday_candles_5min. Auto-triggers gap fill on first page load (before=None)."""
    db_tickers = _normalize_ticker(ticker)
    
    if interval not in ("1m", "5m", "15m", "30m", "1h"):
        return JSONResponse(status_code=400, content={"error": f"Unsupported interval: {interval}"})
        
    model = models.Candle
    q_base = db.query(model).filter(model.ticker.in_(db_tickers), model.timeframe == interval)

    now_epoch = _ts_to_epoch(database.get_ist_now())
    is_latest_request = before is None or before >= (now_epoch - 300)

    # === REGISTER VIEWER for live higher-timeframe candle building ===
    if is_latest_request and interval in ("15m", "30m", "1h"):
        clean_t = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
        live_timeframe_manager.add_viewer(clean_t, interval)

    # Now execute the main query
    q = q_base
    if before is not None:
        q = q.filter(model.timestamp < _epoch_to_ist_dt(before))
    if after is not None:
        q = q.filter(model.timestamp > _epoch_to_ist_dt(after))
    
    records = q.order_by(model.timestamp.desc()).limit(limit + 1).all()

    # === AUTO GAP FILL (Synchronous on first page load) ===
    # Check for gaps inside the fetched data (handles cases where a few recent live candles hide a massive historical gap)
    if is_latest_request:
        try:
            now = database.get_ist_now()
            ist_time = now.time()
            market_open = ist_time >= __import__('datetime').time(9, 15)
            bucket_min = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(interval, 5)
            
            clean_ticker = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
            
            if not records:
                # No data exists at all for this timeframe, schedule background backfill (14 days)
                last_stored_dt = now - __import__('datetime').timedelta(days=14)
                print(f"[GapFill] BG trigger for {clean_ticker} {interval}: completely empty DB")
                if background_tasks is not None:
                    background_tasks.add_task(perform_on_demand_backfill, clean_ticker, interval, last_stored_dt, now)
                else:
                    perform_on_demand_backfill(clean_ticker, interval, last_stored_dt, now)
            else:
                # 1. Check tip gap
                tip_gap_minutes = (now - records[0].timestamp).total_seconds() / 60
                has_tip_gap = (tip_gap_minutes > (bucket_min * 2) and market_open) or (tip_gap_minutes > 375)

                # 2. Check internal gaps
                oldest_gap_start = None
                for i in range(len(records) - 1):
                    # records are ordered newest to oldest
                    t_new = records[i].timestamp
                    t_old = records[i+1].timestamp
                    internal_gap = (t_new - t_old).total_seconds() / 60
                    same_day = t_new.date() == t_old.date()
                    # If gap is larger than 1 trading day (375 mins), we found a missing day/session
                    # If on the same day and gap is larger than interval * 2, we missed an intraday candle
                    if internal_gap > 375 or (same_day and internal_gap > bucket_min * 1.5):
                        oldest_gap_start = t_old
                        # Keep looping to find the absolute oldest gap in the fetched records so we fill them all at once

                if has_tip_gap or oldest_gap_start:
                    fill_start = oldest_gap_start if oldest_gap_start else records[0].timestamp
                    print(f"[GapFill] BG trigger for {clean_ticker} {interval}: filling from {fill_start}")
                    if background_tasks is not None:
                        background_tasks.add_task(perform_on_demand_backfill, clean_ticker, interval, fill_start, now)
                    else:
                        perform_on_demand_backfill(clean_ticker, interval, fill_start, now)
        except Exception as _gf_err:
            print(f"[GapFill] Error checking gap: {_gf_err}")

    has_more = len(records) > limit
    if has_more:
        records = records[:limit]
    data = [{"time": _ts_to_epoch(r.timestamp), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_int(r.volume)} for r in records]
    data.reverse()

    # === ON-THE-FLY RESAMPLING FALLBACK ===
    if is_latest_request and interval in ("15m", "30m", "1h"):
        now = database.get_ist_now()
        last_dt = records[0].timestamp if records else (now - __import__('datetime').timedelta(days=14))
        tip_gap_minutes = (now - last_dt).total_seconds() / 60
        bucket_min = {"15m": 15, "30m": 30, "1h": 60}.get(interval, 15)
        
        # If there's still a noticeable gap after attempted backfills, resample from 5m on the fly
        if tip_gap_minutes > bucket_min * 2:
            try:
                from resampler import CandleResampler
                clean_ticker = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
                five_min_rows = db.query(models.Candle).filter(
                    models.Candle.ticker.in_(db_tickers),
                    models.Candle.timeframe == "5m",
                    models.Candle.timestamp > last_dt
                ).order_by(models.Candle.timestamp.asc()).all()
                
                if five_min_rows:
                    five_min_dicts = [{
                        "timestamp": r.timestamp,
                        "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close),
                        "volume": int(r.volume)
                    } for r in five_min_rows]
                    resampled = CandleResampler.resample_5m_to(five_min_dicts, interval)
                    
                    if resampled:
                        for r in resampled:
                            ts = r.get("timestamp")
                            if isinstance(ts, str):
                                ts = datetime.fromisoformat(ts)
                            data.append({
                                "time": _ts_to_epoch(ts),
                                "open": _safe_float(r["open"]),
                                "high": _safe_float(r["high"]),
                                "low": _safe_float(r["low"]),
                                "close": _safe_float(r["close"]),
                                "volume": _safe_int(r["volume"])
                            })
                        print(f"[GapFill] On-the-fly appended {len(resampled)} {interval} candles for {clean_ticker}")
            except Exception as e:
                print(f"[GapFill] On-the-fly resample error: {e}")

    live = None
    clean_ticker_live = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    if is_latest_request:
        if interval == "5m":
            # 5m live candle from candle_aggregator directly
            live = candle_aggregator.get_current(clean_ticker_live)
            if live and interval in live:
                lc = live[interval]
                if data and lc["time"] == data[-1]["time"]:
                    data[-1] = lc
                elif lc["time"] > (data[-1]["time"] if data else 0):
                    data.append(lc)
        elif interval in ("15m", "30m", "1h"):
            # Higher-TF live forming candle from LiveTimeframeManager
            snapshot = live_timeframe_manager.get_current(clean_ticker_live, interval)
            if snapshot:
                forming = snapshot.get("forming")
                completed = snapshot.get("completed", [])
                ts_set = {c["time"] for c in data}
                for c in completed:
                    if c.get("time") not in ts_set:
                        data.append(c)
                if forming and forming.get("time") not in ts_set:
                    data.append(forming)
                data.sort(key=lambda c: c.get("time", 0))

    return {"data": data, "has_more": has_more}


@app.get("/api/stock-data/range/paginated")
def get_range_paginated(ticker: str = Query(...), range: str = Query("ALL"), before: str = Query(None), after: str = Query(None), limit: int = Query(1500, ge=1, le=50000), db: Session = Depends(get_db)):
    """Paginated daily OHLCV. Reads from candles(1D) — the SSOT for daily chart data.
    Response format is preserved: time=YYYY-MM-DD string (toTimeNum handles both formats)."""
    db_tickers = _normalize_ticker(ticker)
    q = db.query(models.Candle).filter(
        models.Candle.ticker.in_(db_tickers),
        models.Candle.timeframe == '1D',
    )
    if before is not None:
        # before is a date string "YYYY-MM-DD"; candles.timestamp is midnight naive datetime
        before_dt = datetime.strptime(before, "%Y-%m-%d")
        q = q.filter(models.Candle.timestamp < before_dt)
    if after is not None:
        after_dt = datetime.strptime(after, "%Y-%m-%d")
        q = q.filter(models.Candle.timestamp > after_dt)
    range_days = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365}.get(range.upper())
    if range_days is not None and before is None and after is None:
        cutoff = database.get_ist_now().date() - timedelta(days=range_days)
        cutoff_dt = datetime(cutoff.year, cutoff.month, cutoff.day)
        q = q.filter(models.Candle.timestamp >= cutoff_dt)
    records = q.order_by(models.Candle.timestamp.desc()).limit(limit + 1).all()
    has_more = len(records) > limit
    if has_more:
        records = records[:limit]
    # Return date string for time (same format as before; toTimeNum handles it as UTC midnight)
    data = [{"time": str(r.timestamp.date()), "open": _safe_float(r.open), "high": _safe_float(r.high),
             "low": _safe_float(r.low), "close": _safe_float(r.close),
             "adj_close": _safe_float(r.close), "volume": _safe_int(r.volume)} for r in records]
    data.reverse()
    return {"data": data, "has_more": has_more}


@app.get("/api/stock-data/intraday/since")
def get_intraday_since(ticker: str = Query(...), interval: str = Query("5m"), since: int = Query(None), db: Session = Depends(get_db)):
    """Return all intraday candles since a given timestamp (used for WS reconnect recovery)."""
    if since is None:
        return []
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    if _chart_service is None:
        return []
    
    result = _chart_service.get_chart(clean, interval, start=since, limit=5000)
    if isinstance(result, dict) and "candles" in result:
        return result["candles"]
    elif isinstance(result, list):
        return result
    return []


@app.post("/api/holidays/refresh")
def refresh_holidays(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """Fetch latest NSE holiday calendar and update DB + aggregator."""
    import httpx
    from bs4 import BeautifulSoup
    try:
        resp = httpx.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            holidays_added = 0
            for entry in data.get("FO", []):  # F&O segment holidays = full market closure
                d_str = entry.get("tradingDate")
                if d_str:
                    try:
                        d = datetime.strptime(d_str, "%d-%b-%Y").date()
                    except ValueError:
                        d = datetime.strptime(d_str, "%Y-%m-%d").date()
                    existing = db.query(models.Holiday).filter(models.Holiday.date == d).first()
                    if not existing:
                        db.add(models.Holiday(date=d, description=entry.get("description", "NSE Holiday")))
                        holidays_added += 1
            if holidays_added:
                db.commit()
                # Reload into aggregator
                all_h = db.query(models.Holiday.date).all()
                new_holidays = {h[0] for h in all_h}
                with _holidays_lock:
                    NSE_HOLIDAYS.clear()
                    NSE_HOLIDAYS.update(new_holidays)
                candle_aggregator.set_holidays(NSE_HOLIDAYS)
                # Decision 4: nse_calendar (used by execution_engine/trade_service/
                # price_provider/retention_service for real trading decisions) had
                # its own, never-populated holiday set — verified by grep, nothing
                # called load_holidays() anywhere. NSE_HOLIDAYS is now the single
                # source feeding both consumers, closing that desync.
                nse_calendar.load_holidays(NSE_HOLIDAYS)
                candle_aggregator.set_special_sessions(nse_calendar.special_sessions_as_bounds())
            return {"ok": True, "holidays_added": holidays_added, "total": len(NSE_HOLIDAYS)}
        return {"ok": False, "error": f"NSE API returned {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==================== CANDLE HEALTH & GAPS ====================

@app.get("/api/candle-health")
def get_candle_health(ticker: str = Query(None), db: Session = Depends(get_db)):
    """Data quality dashboard: count, timeframes, recent activity."""
    from aggregator import compute_candle_health
    return compute_candle_health(ticker=ticker, db_session=db)


@app.get("/api/candle-gaps")
def get_candle_gaps(
    ticker: str = Query(...),
    timeframe: str = Query("5m"),
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Detect missing intervals for a ticker+timeframe."""
    from aggregator import detect_missing_intervals
    end = date.today()
    start = end - timedelta(days=days)
    gaps = detect_missing_intervals(ticker, timeframe, db, start_date=start, end_date=end)
    return {"ticker": ticker, "timeframe": timeframe, "gaps": gaps, "gap_count": len(gaps)}


@app.post("/api/backfill-symbol/{ticker}")
def backfill_symbol(ticker: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """On-demand backfill for any symbol. Fetches recent data from yfinance."""
    import threading
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    t = threading.Thread(target=_startup_backfill, args=([clean], database.get_ist_now()), daemon=True)
    t.start()
    return {"ok": True, "ticker": clean, "message": "Backfill started in background"}


@app.post("/api/validate-intraday_candles_5min/{ticker}")
def validate_intraday_candles_5min(ticker: str, timeframe: str = Query("1D"), db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """Validate all intraday_candles_5min for a ticker against yfinance for reconciliation."""
    from aggregator import fix_ohlc, validate_ohlc
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    from models import Candle

    intraday_candles_5min = db.query(Candle).filter(
        Candle.ticker == clean,
        Candle.timeframe == timeframe,
        Candle.is_completed == True,
    ).order_by(Candle.timestamp.asc()).limit(1000).all()

    if not intraday_candles_5min:
        return {"ok": True, "ticker": clean, "checked": 0, "mismatches": [], "message": "No data to validate"}

    # Fetch yfinance data for comparison
    try:
        yf_t = _yfinance_ticker(clean)
        yf_data = yf.download(yf_t, period="6mo", interval="1d", progress=False, auto_adjust=True)
    except Exception:
        yf_data = None

    mismatches = []
    invalid_ohlc = 0
    for c in intraday_candles_5min:
        valid, err = validate_ohlc(c.open, c.high, c.low, c.close)
        if not valid:
            invalid_ohlc += 1
            mismatches.append({
                "ts": _ts_to_epoch(c.timestamp),
                "issue": f"invalid OHLC: {err}",
                "open": c.open, "high": c.high, "low": c.low, "close": c.close,
            })

    return {
        "ok": True,
        "ticker": clean,
        "timeframe": timeframe,
        "checked": len(intraday_candles_5min),
        "invalid_ohlc": invalid_ohlc,
        "mismatches": mismatches[:50],
    }


# ==================== MARKET SESSIONS ====================

@app.get("/api/sessions/today")
def get_today_session(db: Session = Depends(get_db)):
    """Return market session info for today."""
    from aggregator import is_trading_day
    from models import MarketSession
    today = date.today()
    session = db.query(MarketSession).filter(
        MarketSession.session_date == today
    ).first()
    if session:
        return {
            "date": str(today),
            "type": session.session_type,
            "open": session.open_time.isoformat(),
            "close": session.close_time.isoformat(),
            "description": session.description,
        }
    # Fallback to hardcoded NSE defaults
    if is_trading_day(today):
        open_dt = datetime(today.year, today.month, today.day, 9, 15, 0)
        close_dt = datetime(today.year, today.month, today.day, 15, 30, 0)
        return {
            "date": str(today),
            "type": "NORMAL",
            "open": open_dt.isoformat(),
            "close": close_dt.isoformat(),
            "description": "Regular trading session",
        }
    return {"date": str(today), "type": "CLOSED", "description": "Market closed"}


@app.api_route("/api/sessions/refresh", methods=["GET", "POST"])
def refresh_sessions_from_nse(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """Scrape NSE for holiday/session data and update market_sessions table."""
    import httpx
    from models import MarketSession
    try:
        resp = httpx.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            added = 0
            for entry in data.get("FO", []):
                d_str = entry.get("tradingDate")
                if d_str:
                    try:
                        d = datetime.strptime(d_str, "%d-%b-%Y").date()
                    except ValueError:
                        d = datetime.strptime(d_str, "%Y-%m-%d").date()
                    existing = db.query(MarketSession).filter(
                        MarketSession.session_date == d
                    ).first()
                    if not existing:
                        open_dt = datetime(d.year, d.month, d.day, 9, 15, 0)
                        close_dt = datetime(d.year, d.month, d.day, 15, 30, 0)
                        db.add(MarketSession(
                            session_date=d,
                            session_type="NORMAL",
                            open_time=open_dt,
                            close_time=close_dt,
                            description=entry.get("description", "Trading day"),
                        ))
                        added += 1
            if added:
                db.commit()
            return {"ok": True, "sessions_added": added}
        return {"ok": False, "error": f"NSE returned {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_screener_cache = {"data": None, "ts": 0.0}
_screener_cache_lock = threading.Lock()

_crypto_cache = {"data": None, "ts": 0.0}
_crypto_cache_lock = threading.Lock()

@app.get("/api/crypto-prices")
async def get_crypto_prices():
    """Return live crypto prices from CoinGecko (free, no API key needed)."""
    now = time.time()
    with _crypto_cache_lock:
        if _crypto_cache["data"] and now - _crypto_cache["ts"] < 60:
            return _crypto_cache["data"]
    try:
        import aiohttp
        ids = "bitcoin,ethereum,ripple,cardano,solana,dogecoin,polkadot,chainlink,avalanche-2,litecoin"
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
        result = []
        mapping = {
            "bitcoin": "BTC", "ethereum": "ETH", "ripple": "XRP",
            "cardano": "ADA", "solana": "SOL", "dogecoin": "DOGE",
            "polkadot": "DOT", "chainlink": "LINK", "avalanche-2": "AVAX", "litecoin": "LTC"
        }
        for cg_id, symbol in mapping.items():
            d = data.get(cg_id, {})
            price = d.get("usd", 0)
            change_24h = d.get("usd_24hr_change", 0)
            mcap = d.get("usd_market_cap", 0)
            result.append({
                "symbol": symbol, "name": cg_id.replace("-2","").replace("-"," ").title(),
                "price": round(price, 2) if price < 1000 else round(price, 0),
                "change_24h": round(change_24h, 2) if change_24h else 0,
                "market_cap": mcap
            })
        with _crypto_cache_lock:
            _crypto_cache["data"] = result
            _crypto_cache["ts"] = time.time()
        return result
    except Exception as e:
        print(f"[Crypto] Error: {e}")
        return _crypto_cache["data"] or []

def _compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    avg_gain = gains / period
    avg_loss = losses / period
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0
        loss = -diff if diff < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
    return round(rsi, 1)

@app.get("/api/screener")
async def get_screener(search: Optional[str] = None, db: Session = Depends(get_db)):
    """Return screener data. Works both during market hours and after close.

    Priority for price data:
      1. AngelOne WS live ticks  (during market hours)
      2. yfinance batch fetch    (market hours fallback)
      3. StockData last close    (after market / weekend / no live data)

    Ticker universe: ALL stocks in stock_metadata (not just premium).
    If MOVER_TICKERS is empty (no premium flag set), fall back to all stocks.
    """
    try:
        # ── 1. Build ticker universe ────────────────────────────────────────
        # Use MOVER_TICKERS if populated, otherwise load ALL tickers from DB
        if search:
            search_clean = search.strip().upper()
            from sqlalchemy import or_
            rows = db.query(models.StockMetadata.ticker).filter(
                or_(
                    models.StockMetadata.ticker.ilike(f"%{search_clean}%"),
                    models.StockMetadata.name.ilike(f"%{search_clean}%")
                )
            ).limit(100).all()
            stocks = [r[0] for r in rows]
        elif MOVER_TICKERS:
            stocks = MOVER_TICKERS[:200]
        else:
            try:
                from sqlalchemy import text as _sa_txt
                # Query stocks that actually have candles data in our DB first
                rows = db.execute(_sa_txt(
                    "SELECT ticker FROM stock_metadata "
                    "WHERE ticker IN (SELECT DISTINCT ticker FROM candles WHERE timeframe = '1D') "
                    "ORDER BY ticker LIMIT 200"
                )).fetchall()
                stocks = [r[0] for r in rows]
                
                # Fallback to general stock_metadata if candles table has too few tickers
                if len(stocks) < 20:
                    rows_fallback = db.execute(_sa_txt("SELECT ticker FROM stock_metadata ORDER BY ticker LIMIT 200")).fetchall()
                    stocks = list(set(stocks + [r[0] for r in rows_fallback]))
            except Exception:
                stocks = []

        if not stocks or len(stocks) < 10:
            fallback = ['RELIANCE','TCS','HDFCBANK','INFY','ICICIBANK','SBIN',
                        'BHARTIARTL','ITC','WIPRO','LT','HINDUNILVR','MARUTI',
                        'DRREDDY','HCLTECH','AXISBANK','BAJFINANCE','KOTAKBANK',
                        'TITAN','ASIANPAINT','NTPC','SUNPHARMA','ULTRACEMCO',
                        'ONGC','POWERGRID','M&M','TATAMOTORS','TATASTEEL','JSWSTEEL',
                        'ADANIPORTS','GRASIM']
            for fb in fallback:
                if fb not in stocks:
                    stocks.append(fb)

        # ── 2. Serve cached data if fresh (< 5 min) and NOT performing a search ──
        if not search:
            now_ts = time.time()
            with _screener_cache_lock:
                cached = _screener_cache.get("data")
                cached_ts = _screener_cache.get("ts", 0.0)
            if cached and (now_ts - cached_ts) < 300:
                return cached

        # ── 3. Fetch live prices (pass market_open=True so yfinance tries) ──
        market_open = is_market_open_now()
        prices = await fetch_batch_live_data(stocks, market_open=market_open)

        # ── 4. Fetch historical data from DB for RSI + avg volume ────────────
        from datetime import timedelta

        avg_vol_cache: dict = {}   # ticker → (avg_volume, closes_list)
        last_candle_cache: dict = {}  # ticker → last candles(1D) row (fallback price)

        def _fetch_screener_db_data():
            # 4a. Daily historical data for RSI + volume ratio — from candles(1D)
            cutoff = datetime.now(IST).date() - timedelta(days=60)
            cutoff_dt = datetime(cutoff.year, cutoff.month, cutoff.day)
            all_historical = db.query(models.Candle).filter(
                models.Candle.ticker.in_(stocks),
                models.Candle.timeframe == '1D',
                models.Candle.timestamp >= cutoff_dt,
            ).order_by(models.Candle.timestamp.desc()).all()

            grouped: dict = {}
            for row in all_historical:
                if row.ticker not in grouped:
                    grouped[row.ticker] = []
                if len(grouped[row.ticker]) < 21:
                    grouped[row.ticker].append(row)

            for t, c_list in grouped.items():
                c_list.reverse()
                closes = [c.close for c in c_list if c.close and c.close > 0]
                volumes = [c.volume for c in c_list if c.volume and c.volume > 0]
                avg_v = sum(volumes[:-1]) / (len(volumes) - 1) if len(volumes) >= 5 else None
                avg_vol_cache[t] = (avg_v, closes)

            # 4b. Last candle for any ticker with no live price
            tickers_missing = [t for t in stocks if not prices.get(t, {}).get("current_price")]
            if tickers_missing:
                for t in tickers_missing:
                    if t in grouped and len(grouped[t]) > 0:
                        last_candle_cache[t] = grouped[t][-1]  # most recent after reverse()


        await asyncio.to_thread(_fetch_screener_db_data)

        # ── 5. Build result rows ─────────────────────────────────────────────
        result = []
        for ticker in stocks:
            p = prices.get(ticker, {})
            cp = _safe_float(p.get("current_price") or p.get("current"))

            # Fallback: use last historical close from candles(1D)
            using_historical = False
            if cp == 0:
                sd = last_candle_cache.get(ticker)
                if sd and sd.close and sd.close > 0:
                    cp = float(sd.close)
                    using_historical = True
                    # Reconstruct change from open/prev_close in candle row
                    prev_c = float(sd.close)   # candles has no adj_close; use close
                    open_p = float(sd.open or sd.close)
                    # Use prior day close if available from candles
                    _, closes_hist = avg_vol_cache.get(ticker, (None, []))
                    if len(closes_hist) >= 2:
                        prev_c = closes_hist[-2]
                    p = {
                        "current_price": cp,
                        "prev_close": prev_c,
                        "open": open_p,
                        "volume": _safe_int(sd.volume),
                    }
                else:
                    continue  # truly no data at all

            prev_close = _safe_float(p.get("prev_close"))
            open_p     = _safe_float(p.get("open"))
            change     = round(cp - (prev_close if prev_close else open_p if open_p else cp), 2)
            change_pct = round((change / prev_close * 100), 2) if prev_close and prev_close != 0 else 0.0
            vol        = _safe_int(p.get("volume"))

            meta   = STOCK_META.get(ticker, {})
            name   = meta.get("name", ticker)
            logo   = _safe_logo(meta.get("logo", ""))
            sector = _SECTOR_MAP.get(ticker, "Unknown")

            avg_v, closes = avg_vol_cache.get(ticker, (None, []))
            avg_vol = avg_v if avg_v is not None else (vol if vol > 0 else 0)

            vol_ratio = round(vol / avg_vol, 2) if avg_vol > 0 and vol > 0 else 1.0
            rsi = _compute_rsi(closes) if len(closes) >= 15 else None

            result.append({
                "ticker":      ticker,
                "name":        name,
                "logo":        logo,
                "sector":      sector,
                "price":       round(cp, 2),
                "change":      change,
                "change_pct":  change_pct,
                "volume":      vol,
                "avg_volume":  round(avg_vol, 1) if avg_vol else 0,
                "vol_ratio":   vol_ratio,
                "rsi":         rsi,
                "source":      "historical" if using_historical else p.get("source", "live"),
            })

        # Sort by absolute % change descending
        result.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

        print(f"[Screener] Returning {len(result)} stocks "
              f"({sum(1 for r in result if r['source']=='historical')} from StockData fallback)")

        with _screener_cache_lock:
            _screener_cache["data"] = result
            _screener_cache["ts"]   = time.time()
        return result

    except Exception as e:
        import traceback
        err_str = f"[Screener] Error: {e}\n{traceback.format_exc()}"
        print(err_str)
        try:
            with open("screener_error.log", "w") as f:
                f.write(err_str)
        except Exception:
            pass
        # Return stale cache rather than empty on error
        with _screener_cache_lock:
            stale = _screener_cache.get("data")
        return stale or []



@app.get("/api/time")
def get_server_time():
    """Return server UTC epoch seconds for TradingView getServerTime."""
    return {"server_time": int(time.time())}

# Removed intentional /sentry-debug ZeroDivisionError — was polluting Sentry with noise


# ==================== STATIC FILES MOUNT ====================

from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

def _startup_backfill(tickers: list, now: datetime):
    """Background pre-warm yfinance cache for tickers (intraday not persisted to DB).
    Warms both index and stock tickers so first user chart load is instant."""
    from aggregator import snap_to_nse_session, fix_ohlc
    import yfinance as yf
    warm = [t for t in tickers if t]
    if not warm:
        return
    for ticker in warm:
        clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
        for interval in ("5m", "15m"):
            try:
                _fetch_yfinance_intraday(None, clean, interval, use_bg_semaphore=True)
            except Exception as e:
                print(f"[PreWarm] Error {clean} {interval}: {e}")


def _on_angel_tick(ticker: str, data: dict):
    """Called from AngelOne WS thread — buffers tick for async broadcast + feeds aggregator."""
    global _last_angel_tick_time
    _last_angel_tick_time = time.time()
    _last_angel_ts_per_ticker[ticker] = time.time()
    try:
        cp = data.get("current_price", 0)
        pc = data.get("prev_close", 0)
        change = round(cp - pc, 2) if pc else 0
        change_pct = round(((cp - pc) / pc) * 100, 2) if pc and pc != 0 else 0
        with _angel_tick_buffer_lock:
            tick_open = data.get("open")
            tick_high = data.get("high")
            tick_low = data.get("low")
            daily_volume = data.get("volume", 0)
            actual_tick_volume = data.get("tick_volume", 0)
            _angel_tick_buffer[ticker] = {
                "current_price": cp,
                "current": cp,
                "prev_close": pc,
                "change": change,
                "change_pct": change_pct,
                "volume": daily_volume,
                "_source": "angel_ws",
                "_ts": time.time(),
            }
            if tick_open is not None and tick_open > 0: _angel_tick_buffer[ticker]["open"] = tick_open
            if tick_high is not None and tick_high > 0: _angel_tick_buffer[ticker]["high"] = tick_high
            if tick_low is not None and tick_low > 0:  _angel_tick_buffer[ticker]["low"]  = tick_low

        # ── Feed tick into aggregator for candle formation ──────────────
        # Pass the broker timestamp (if available) for stale-tick detection
        tick_epoch = data.get("_ts", time.time())
        if cp > 0:
            candle_aggregator.process_tick(
                ticker, cp, actual_tick_volume, tick_ts=tick_epoch,
                day_open=tick_open, day_high=tick_high, day_low=tick_low
            )
            _last_aggregator_feed_ts[ticker] = tick_epoch
            _monitoring["aggregator_ticks_processed"] += 1
    except Exception as e:
        print(f"[AngelTick] _on_angel_tick error for {ticker}: {e}")

async def _broadcast_angel_ticks():
    """Fast loop: flushes AngelOne WS tick buffer to dashboard clients every ~200ms.
    Uses a low-latency adaptive sleep: 200ms when ticks flow, 1s when idle.
    Instantly wakes when new ticks arrive (next loop iteration)."""
    while True:
        try:
            batch = None
            with _angel_tick_buffer_lock:
                if _angel_tick_buffer:
                    batch = dict(_angel_tick_buffer)
                    _angel_tick_buffer.clear()
            if batch:
                await manager.broadcast({
                    "type": "price_update",
                    "data": batch,
                    "ts": datetime.now(IST).isoformat()
                })
                await asyncio.sleep(0.1)  # 100ms while ticks flow (was 200ms)
            else:
                await asyncio.sleep(1.0)   # 1s when idle (was 500ms)
        except Exception as e:
            print(f"[AngelTick] Broadcast error: {e}")
            await asyncio.sleep(1.0)

@app.on_event("startup")
async def startup():
    """Load NSE holidays + start dashboard broadcast loops + subscribe AngelOne WS."""
    global viewed_ticker_mgr
    viewed_ticker_mgr = ViewedTickerManager(angelone_service, max_subscriptions=2000)
    threading.Thread(target=viewed_ticker_mgr._process_pending, daemon=True).start()

    try:
        models.Base.metadata.create_all(bind=database.engine)
    except Exception as e:
        print(f"[Startup] Could not create tables: {e}")

    # ── Migration v005: add is_premium column + seed premium tickers ──
    db_migrate = None
    try:
        from database import SessionLocal
        db_migrate = SessionLocal()
        from sqlalchemy import text as sa_text
        # Add columns if not exists (model was updated but DB wasn't migrated)
        for col, col_type in [("is_active", "BOOLEAN NOT NULL DEFAULT TRUE"), ("is_premium", "BOOLEAN NOT NULL DEFAULT FALSE")]:
            try:
                db_migrate.execute(sa_text(f"ALTER TABLE stock_metadata ADD COLUMN IF NOT EXISTS {col} {col_type}"))
            except Exception:
                # IF NOT EXISTS is PG 9.6+; fallback for older versions
                db_migrate.rollback()
                col_exists = db_migrate.execute(sa_text(
                    f"SELECT column_name FROM information_schema.columns WHERE table_name='stock_metadata' AND column_name='{col}'"
                )).fetchone()
                if not col_exists:
                    db_migrate.execute(sa_text(f"ALTER TABLE stock_metadata ADD COLUMN {col} {col_type}"))
        db_migrate.commit()
        # Seed premium tickers only if none are flagged yet (avoids 213 UPDATEs on every restart)
        existing = db_migrate.execute(sa_text("SELECT COUNT(*) FROM stock_metadata WHERE is_premium = TRUE")).scalar()
        if existing == 0:
            premium_tickers = [
                'AAATECH','AARON','ABFRL','ABLBL','ADANIENT','ADANIPORTS','AEROFLEX','AKSHAR',
                'ALOKINDS','AMBUJACEM','ARSSBL','ARTEMISMED','ASIANPAINT','ATALREAL','AVL',
                'AXISBANK','AXISGOLD','AXITA','BAJAJHFL','BAJFINANCE','BANKNIFTY','BELRISE',
                'BHARTIARTL','BHARTIHEXA','BIOCON','CANFINHOME','CANHLIFE','CASTROLIND',
                'CENTURYPLY','CERA','COALINDIA','COLPAL','DCXINDIA','DGCONTENT','DIGIDRIVE',
                'DOMS','DPEL','DREDGECORP','DRREDDY','DWARKESH','ELECTCAST','EMBDL','ESCORTS',
                'ETERNAL','EXIDEIND','FABTECH','FEDERALBNK','FMCGIETF','GABRIEL','GAIL',
                'GATECH','GENCON','GILLETTE','GKENERGY','GMDCLTD','GMRAIRPORT','GODREJAGRO',
                'GOLDCASE','GRASIM','GREENPOWER','GRINDWELL','HBLENGINE','HCLTECH','HDFCBANK',
                'HDFCGOLD','HDFCLIQUID','HDFCMID150','HDFCPVTBAN','HINDALCO','HINDUNILVR',
                'HINDZINC','HOMEFIRST','HPAL','ICICIBANK','ICRA','IEX','INDUSTOWER','INFY',
                'INOXWIND','INTENTECH','IOC','IRCON','IRCTC','IREDA','IRFC','ITC','JAINREC',
                'JARO','JAYNECOIND','JINDWORLD','JISLJALEQS','JSWCEMENT','JSWSTEEL',
                'JUBLFOOD','JYOTISTRUC','KALAMANDIR','KOTAKBANK','KSHINTL','LANCORHOL',
                'LEMERITE','LICI','LINC','LIQUIDCASE','LLOYDSENGG','LT','LTF','LYKALABS',
                'M&MFIN','MAANALU','MANAKCOAT','MARKOLINES','MARUTI','METALIETF','MIDCAP',
                'MOIL','MRPL','MSUMI','MULTICAP','NATIONALUM','NAZARA','NDRAUTO','NEWGEN',
                'NHPC','NIFTY','NMDC','NOCIL','NORTHARC','NSLNISP','NTPC','NTPCGREEN','OIL',
                'OILIETF','ONGC','ORIENTCEM','PAISALO','PAR','PARADEEP','PCJEWELLER',
                'PINELABS','PIRAMALFIN','POCL','POWERGRID','PTCIL','PVTBANKADD','PWL',
                'QUINT','RAILTEL','RAYMOND','RBLBANK','RCF','RELIANCE','RELTD','RENUKA',
                'ROLEXRINGS','SAATVIKGL','SADBHAV','SAGILITY','SAIL','SAMMAANCAP','SANDUMA',
                'SBC','SBIN','SBISILVER','SCHAND','SENSEX','SETFGOLD','SHAILY','SHAKTIPUMP',
                'SHRIRAMFIN','SHYAMCENT','SILVERBEES','SJVN','SMALLCAP','SOUTHBANK',
                'SPECIALITY','STALLION','STEELXIND','SUNPHARMA','SURYAROSNI','SURYODAY',
                'SUTLEJTEX','TARIL','TATAELXSI','TATASTEEL','TCIEXP','TCS','TEXRAIL',
                'TIMETECHNO','TIMKEN','TIPSFILMS','TITAGARH','TITAN','TMCV','TMPV','TPHQ',
                'TRIDENT','UFO','UJJIVANSFB','ULTRACEMCO','ULTRAMAR','UNIONBANK','UNITDSPR',
                'UNITECH','URBANCO','VEDL','VIDYAWIRES','VINCOFE','VSTTILLERS','WALCHANNAG',
                'WIPRO','YESBANK','ZEEL','ZEEMEDIA'
            ]
            for i in range(0, len(premium_tickers), 500):
                chunk = premium_tickers[i:i+500]
                placeholders = ",".join(f":t{j}" for j in range(len(chunk)))
                params = {f"t{j}": t for j, t in enumerate(chunk)}
                db_migrate.execute(
                    sa_text(f"UPDATE stock_metadata SET is_premium = TRUE WHERE ticker IN ({placeholders})"),
                    params
                )
            db_migrate.commit()
            print(f"[Startup] Seeded {len(premium_tickers)} premium tickers")
    except Exception as e:
        if db_migrate: db_migrate.rollback()
        print(f"[Startup] Migration v005 error: {e}")
    finally:
        if db_migrate: db_migrate.close()

    # ── Migration v006: add sector column + load sector map ──
    db_migrate2 = None
    try:
        from database import SessionLocal
        db_migrate2 = SessionLocal()
        from sqlalchemy import text as sa_text2
        for col, col_type in [("sector", "VARCHAR(100)")]:
            try:
                db_migrate2.execute(sa_text2(f"ALTER TABLE stock_metadata ADD COLUMN IF NOT EXISTS {col} {col_type}"))
            except Exception:
                db_migrate2.rollback()
                col_exists = db_migrate2.execute(sa_text2(
                    f"SELECT column_name FROM information_schema.columns WHERE table_name='stock_metadata' AND column_name='{col}'"
                )).fetchone()
                if not col_exists:
                    db_migrate2.execute(sa_text2(f"ALTER TABLE stock_metadata ADD COLUMN {col} {col_type}"))
        db_migrate2.commit()

        # Load sector_data.json into memory
        sector_json_path = os.path.join(os.path.dirname(__file__), "sector_data.json")
        if os.path.exists(sector_json_path):
            with open(sector_json_path, "r") as f:
                raw_sector_map = json.load(f)
            _SECTOR_MAP.clear()
            _SECTOR_MAP.update({k.upper(): v for k, v in raw_sector_map.items()})
            # Build reverse map: sector -> list of tickers (for sector leaders fallback)
            _sector_tickers.clear()
            for tkr, sec in _SECTOR_MAP.items():
                if sec not in _sector_tickers:
                    _sector_tickers[sec] = []
                _sector_tickers[sec].append(tkr)
            print(f"[Startup] Loaded {len(_SECTOR_MAP)} sector mappings across {len(_sector_tickers)} sectors from sector_data.json")

            # Backfill sector column for rows where it's NULL
            for ticker, sector in _SECTOR_MAP.items():
                db_migrate2.execute(
                    sa_text2("UPDATE stock_metadata SET sector = :sector WHERE ticker = :ticker AND sector IS NULL"),
                    {"sector": sector, "ticker": ticker}
                )
            db_migrate2.commit()
            updated = db_migrate2.execute(sa_text2(
                "SELECT COUNT(*) FROM stock_metadata WHERE sector IS NOT NULL"
            )).scalar()
            print(f"[Startup] Backfilled sector for stock_metadata ({updated} rows have sector)")
        else:
            print(f"[Startup] WARNING: sector_data.json not found at {sector_json_path}")
    except Exception as e:
        if db_migrate2: db_migrate2.rollback()
        print(f"[Startup] Migration v006 error: {e}")
    finally:
        if db_migrate2: db_migrate2.close()

    # ── Migration v007: performance indexes ──
    try:
        from database import SessionLocal as SessIdx
        sess_idx = SessIdx()
        index_sql = [
            "CREATE INDEX IF NOT EXISTS ix_candle_ticker_tf_ts ON candles (ticker, timeframe, timestamp DESC)",
            "CREATE INDEX IF NOT EXISTS ix_currentdaycandle_ticker_date ON current_day_candle (ticker, trading_date DESC)",
            "CREATE INDEX IF NOT EXISTS ix_metadata_is_premium ON stock_metadata (is_premium) WHERE is_premium = TRUE",
            "CREATE INDEX IF NOT EXISTS ix_stock_data_ticker_date_desc ON stock_data (ticker, date DESC)",
        ]
        for stmt in index_sql:
            try:
                sess_idx.execute(sa_text(stmt))
            except Exception as idx_e:
                print(f"[Startup] Index error: {idx_e}")
        sess_idx.commit()
        sess_idx.close()
        print("[Startup] Performance indexes created/verified (v007)")
    except Exception as e:
        print(f"[Startup] Migration v007 error: {e}")

    # ── Refresh MOVER_TICKERS from DB (premium stocks + indices) ──
    try:
        from database import SessionLocal as Sess2
        from sqlalchemy import text as sa_text3
        sess2 = Sess2()
        rows = sess2.execute(sa_text3("SELECT ticker FROM stock_metadata WHERE is_premium = TRUE ORDER BY ticker")).fetchall()
        premium = [r[0] for r in rows]
        # Always include key indices
        for idx in ['NIFTY','SENSEX','BANKNIFTY']:
            if idx not in premium:
                premium.append(idx)
        MOVER_TICKERS.clear()
        MOVER_TICKERS.extend(premium)
        sess2.close()
        print(f"[Startup] Loaded {len(MOVER_TICKERS)} mover tickers from DB (premium + indices)")
    except Exception as e:
        print(f"[Startup] Could not load mover tickers from DB: {e}, using fallback")
        if not MOVER_TICKERS:
            MOVER_TICKERS.extend(['RELIANCE','TCS','HDFCBANK','INFY','ICICIBANK','SBIN','BHARTIARTL','ITC','WIPRO','LT','HINDUNILVR','MARUTI','DRREDDY','HCLTECH','AXISBANK','BAJFINANCE','KOTAKBANK','TITAN','ASIANPAINT','NTPC','SUNPHARMA','ULTRACEMCO','ONGC','POWERGRID','M&M','TATAMOTORS','TATASTEEL','JSWSTEEL','ADANIPORTS','GRASIM','NIFTY','SENSEX','BANKNIFTY'])

    # ── Load AngelOne instruments cache so token lookup works for ALL stocks ──
    # Without this, SME stocks (ACETEC, etc) and any ticker not in the ~2400
    # hardcoded tokens returns None from get_token(), making every historical
    # fallback silently skip them → "No chart data" for those stocks.
    try:
        loaded = await asyncio.to_thread(angelone_service.load_instruments)
        if loaded:
            print(f"[Startup] AngelOne instruments loaded — token lookup enabled for all NSE stocks (incl. SME)")
        else:
            print("[Startup] WARNING: AngelOne instruments failed to load — SME/unlisted stocks may show no data")
    except Exception as e:
        print(f"[Startup] AngelOne instruments load error: {e}")

    # ── Load ALL NSE tickers for AngelOne WS subscription (candle building) ──
    try:
        from database import SessionLocal as SessWs
        from sqlalchemy import text as sa_text_ws
        sess_ws = SessWs()
        rows_ws = sess_ws.execute(sa_text_ws(
            "SELECT ticker FROM stock_metadata WHERE exchange = 'NSE' AND (is_active IS NULL OR is_active = TRUE) ORDER BY ticker"
        )).fetchall()
        ALL_WS_TICKERS.clear()
        ALL_WS_TICKERS.extend([r[0] for r in rows_ws])
        sess_ws.close()
        print(f"[Startup] Loaded {len(ALL_WS_TICKERS)} tickers for AngelOne WS candle building")
    except Exception as e:
        print(f"[Startup] Could not load WS tickers: {e}")

    try:
        from database import SessionLocal
        db = SessionLocal()
        holidays = db.query(models.Holiday.date).all()
        new_set = set()
        for (h,) in holidays:
            new_set.add(h)
        with _holidays_lock:
            NSE_HOLIDAYS.clear()
            NSE_HOLIDAYS.update(new_set)
        # Share holidays with the aggregator
        candle_aggregator.set_holidays(NSE_HOLIDAYS)
        # Decision 4: nse_calendar is the calendar consulted by execution_engine,
        # trade_service, price_provider, and retention_service for real trading
        # decisions (order execution, AMO queueing) — it was never fed holidays
        # (verified: no load_holidays() call existed anywhere in the app), so on
        # an actual market holiday those consumers would have seen the market as
        # open. NSE_HOLIDAYS is now the single source for both it and the
        # aggregator. Also forward any special/shortened sessions registered on
        # nse_calendar so the aggregator honors them instead of silently
        # rejecting every tick outside the standard 09:15-15:30 window.
        nse_calendar.load_holidays(NSE_HOLIDAYS)
        candle_aggregator.set_special_sessions(nse_calendar.special_sessions_as_bounds())
        print(f"[Startup] Loaded {len(NSE_HOLIDAYS)} NSE holidays")

        # CAL-01: if table is empty (fresh install or never refreshed),
        # fetch the authoritative list from NSE so the calendar is never
        # blank.  Runs synchronously — startup completes before requests
        # are served, so a brief blocking fetch is acceptable here.
        if len(NSE_HOLIDAYS) < 10:
            print("[Startup] Holidays table has < 10 entries — auto-fetching from NSE...")
            try:
                import requests as _req
                _resp = _req.get(
                    "https://www.nseindia.com/api/holiday-master?type=trading",
                    timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if _resp.status_code == 200:
                    _data = _resp.json()
                    _added = 0
                    for _entry in _data.get("FO", []):
                        _d_str = _entry.get("tradingDate")
                        if _d_str:
                            try:
                                _d = datetime.strptime(_d_str, "%d-%b-%Y").date()
                            except ValueError:
                                _d = datetime.strptime(_d_str, "%Y-%m-%d").date()
                            _exists = db.query(models.Holiday).filter(
                                models.Holiday.date == _d
                            ).first()
                            if not _exists:
                                db.add(models.Holiday(
                                    date=_d,
                                    description=_entry.get("description", "NSE Holiday"),
                                ))
                                _added += 1
                    if _added:
                        db.commit()
                        _new_h = {h[0] for h in db.query(models.Holiday.date).all()}
                        with _holidays_lock:
                            NSE_HOLIDAYS.clear()
                            NSE_HOLIDAYS.update(_new_h)
                        candle_aggregator.set_holidays(NSE_HOLIDAYS)
                        nse_calendar.load_holidays(NSE_HOLIDAYS)
                        candle_aggregator.set_special_sessions(nse_calendar.special_sessions_as_bounds())
                    print(f"[Startup] Auto-seeded {_added} holidays ({len(NSE_HOLIDAYS)} total)")
                else:
                    print(f"[Startup] NSE holiday fetch returned HTTP {_resp.status_code}")
            except Exception as _e:
                print(f"[Startup] Holiday auto-fetch failed (non-fatal): {_e}")

        db.close()
    except Exception as e:
        print(f"[Startup] Could not load holidays: {e}")

    # ── Crash recovery: lazy (per-ticker, on first view) ───────────────
    # RecoveryService handles REST-based recovery on demand.
    # No bulk recovery at startup — recovers only viewed tickers.
    init_recovery_service()
    print(f"[Startup] Lazy recovery ready (recovers tickers on first view)")

    # ── Backfill: fetch recent intraday data for active tickers on startup ──
    db = None
    try:
        from database import SessionLocal
        db = SessionLocal()
        stock_tickers = [r[0] for r in db.query(models.StockMetadata.ticker).limit(50).all()]
        index_tickers = ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCAP", "SMALLCAP"]
        tickers = list(dict.fromkeys(index_tickers + stock_tickers))  # deduplicate, indices first
        if tickers:
            import threading as _th
            now = database.get_ist_now()
            bf = threading.Thread(target=_startup_backfill, args=(tickers, now), daemon=True)
            bf.start()
    except Exception as e:
        print(f"[Startup] Backfill trigger error: {e}")
    finally:
        if db: db.close()

    # ── Pre-populate yfinance failure cache for invalid/delisted tickers ──
    try:
        from database import SessionLocal
        from sqlalchemy import text as sa_text
        _db = SessionLocal()
        all_tickers = [r[0] for r in _db.execute(
            sa_text("SELECT ticker FROM stock_metadata")
        ).fetchall()]
        known_symbols = yf_downloader.get_known_symbols()
        if known_symbols:
            pre_marked = 0
            for tkr in all_tickers:
                is_valid, _ = yf_downloader.validate_symbol(tkr)
                if not is_valid:
                    yf_downloader._mark_failed(tkr, YFErrorClass.NOT_FOUND)
                    pre_marked += 1
            if pre_marked:
                print(f"[Startup] Pre-marked {pre_marked} tickers as failed (not in known NSE symbols)")
        _db.close()
    except Exception as e:
        print(f"[Startup] Failure cache pre-population error: {e}")

    # Pre-populate all-stocks cache so first request doesn't hit DB
    try:
        from database import SessionLocal as _CacheSession
        _cache_db = _CacheSession()
        stocks = _cache_db.query(models.StockMetadata).all()
        _all_stocks_cache = [
            {"ticker": s.ticker, "name": s.name, "logo": _safe_logo(s.logo),
             "exchange": s.exchange, "basePrice": s.base_price}
            for s in stocks
        ]
        _all_stocks_cache_ts = time.time()
        _cache_db.close()
        print(f"[Startup] Pre-populated all-stocks cache with {len(_all_stocks_cache)} stocks")
    except Exception as e:
        print(f"[Startup] Could not pre-populate all-stocks cache: {e}")

    # Wire AngelOne real-time ticks to dashboard broadcast
    angelone_service.on_tick_callback = _on_angel_tick

    def _log_task_error(task):
        try:
            exc = task.exception()
            if exc:
                print(f"[Startup] Background task failed: {exc}")
        except asyncio.CancelledError:
            pass

    # Subscribe to AngelOne WebSocket + start multi-threaded poller for ALL stocks
    try:
        # Run login in thread to avoid blocking the event loop (1-3s HTTP call)
        login_ok = await asyncio.to_thread(angelone_service.login)
        if login_ok:
            # Subscribe only the essential tickers at startup (indices + dashboard + movers).
            # ALL_WS_TICKERS (~3000+) is intentionally excluded: sending thousands of tokens
            # in one burst causes AngelOne to drop/reject the connection, which triggers the
            # reconnect storm. All other tickers are subscribed on-demand via
            # ViewedTickerManager when a user opens their page; gap recovery then backfills
            # any missed candles so the chart is complete.
            startup_ws = list(dict.fromkeys(DASHBOARD_TICKERS + MOVER_TICKERS))
            # RT-08: subscribe_tickers() blocks for up to 15s waiting for the
            # WS handshake -- run off the event loop so startup doesn't stall.
            await asyncio.to_thread(angelone_service.subscribe_tickers, startup_ws)
            print(f"[Startup] Subscribed {len(startup_ws)} core tickers to AngelOne WS (on-demand for others)")

            # Start the multi-threaded REST poller for active tickers (not all 5575)
            # Include dashboard, movers, sector indices, and the top 2 stocks of each sector
            sector_rep = []
            for s, tkrs in _sector_tickers.items():
                sector_rep.extend(tkrs[:2])
            
            active_tickers = list(dict.fromkeys(DASHBOARD_TICKERS + MOVER_TICKERS + SECTOR_TICKERS + sector_rep))
            poller = PricePoller(angelone_service, tickers=active_tickers)
            poller_task = asyncio.create_task(poller.start())
            poller_task.add_done_callback(_log_task_error)
            print(f"[Startup] Multi-threaded price poller started for {len(active_tickers)} tickers")

            # ── Start CriticalIndexPoller ─ dedicated thread for NIFTY/SENSEX/BANKNIFTY/FINNIFTY ──
            # This thread polls only the 6 major indices every 1 second so they are NEVER stale.
            # It runs independently of the bulk PricePoller to avoid any delay from stock polling.
            critical_poller = CriticalIndexPoller(
                angelone_service,
                on_price_update=_on_angel_tick  # same callback → broadcast to dashboard WS
            )
            critical_poller.start()
            app.state.critical_poller = critical_poller
            print(f"[Startup] CriticalIndexPoller started — major indices now update every 1s")

            # Expose poller for stats endpoint
            app.state.price_poller = poller
    except Exception as e:
        print(f"[Startup] AngelOne WS/poller setup failed: {e}")

    # Start WS reconnection loop (checks every 15s normally, 30s when reconnecting)
    async def _ws_watchdog():
        _ws_was_ok = True
        while True:
            # When a reconnect is already in-flight, back off so we don't
            # hammer AngelOne with overlapping connection attempts.
            reconnecting = getattr(angelone_service, '_ws_reconnecting', False)
            await asyncio.sleep(30 if reconnecting else 15)
            try:
                ws_ok = angelone_service.ensure_ws_connected()
                if ws_ok and not _ws_was_ok:
                    _monitoring["ws_reconnects"] += 1
                    print(f"[Watchdog] WS reconnected — checking stale tickers...")
                    now_ts = time.time()
                    stale = {t for t, ts in list(_last_angel_ts_per_ticker.items())
                             if now_ts - ts > 120}
                    if stale:
                        import concurrent.futures
                        stale_list = list(stale)[:100]
                        print(f"[Watchdog] Replaying {len(stale_list)} stale tickers (since last WS tick)...")
                        def _replay_worker(tkr):
                            try:
                                if _recovery_service is not None:
                                    # RT-05: a reconnect-detected stale ticker is a
                                    # fresh signal that recovery is needed right now,
                                    # even if this ticker was already recovered
                                    # earlier in the process lifetime (e.g. a second
                                    # WS drop the same day) -- re-arm it first so
                                    # recover_ticker()'s own one-shot guard doesn't
                                    # silently no-op.
                                    _recovery_service.mark_needs_recovery(tkr)
                                    _recovery_service.recover_ticker(tkr, candle_aggregator)
                                    _monitoring["ws_replay_count"] += 1
                            except Exception:
                                pass
                        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                            pool.map(_replay_worker, stale_list)
                _ws_was_ok = ws_ok
                if ws_ok and _last_angel_tick_time > 0 and is_market_open_now():
                    age = time.time() - _last_angel_tick_time
                    if age > 10:
                        print(f"[Watchdog] WS connected but no ticks for {age:.0f}s (SDK internal retry handles reconnect)")
            except Exception as e:
                print(f"[Watchdog] WS reconnect error: {e}")

    watchdog_task = asyncio.create_task(_ws_watchdog())
    watchdog_task.add_done_callback(_log_task_error)

    # Periodic AngelOne auth refresh (every 30 min) to prevent token expiry
    async def _angel_auth_refresh():
        while True:
            await asyncio.sleep(1800)
            try:
                await angelone_service.ensure_connection()
            except Exception as e:
                print(f"[AuthRefresh] Error: {e}")

    auth_refresh_task = asyncio.create_task(_angel_auth_refresh())
    auth_refresh_task.add_done_callback(_log_task_error)

    # Start broadcast loops
    bc1 = asyncio.create_task(_broadcast_dashboard())
    bc2 = asyncio.create_task(_broadcast_angel_ticks())
    bc1.add_done_callback(_log_task_error)
    bc2.add_done_callback(_log_task_error)

    # Movers snapshot: build once now, then keep it warm on a timer so
    # /api/market-movers never has to scan the whole tick cache in the request path.
    async def _movers_refresh_loop():
        await refresh_movers_snapshot()
        print("[Movers] Initial snapshot built")
        while True:
            await asyncio.sleep(_MOVERS_REFRESH_SEC)
            await refresh_movers_snapshot()
    movers_task = asyncio.create_task(_movers_refresh_loop())
    movers_task.add_done_callback(_log_task_error)

    # Periodic aggregator health monitor (every 5 min)
    async def _aggregator_health_monitor():
        while True:
            await asyncio.sleep(300)
            try:
                candle_aggregator.print_health()
            except Exception as e:
                print(f"[Aggregator] Health monitor error: {e}")
    hc = asyncio.create_task(_aggregator_health_monitor())
    hc.add_done_callback(_log_task_error)

    # REST poller → aggregator bridge: feeds aggregator from poller data
    # Runs every 2s on the event loop. The 'last_feed' timestamp deduplicates
    # so WS ticks (faster) and poller ticks (slower, 2s cycle) don't double-count.
    async def _poller_aggregator_bridge():
        while True:
            try:
                with angelone_service.latest_ticks_lock:
                    ticks = dict(angelone_service.latest_ticks)
                for tkr, td in ticks.items():
                    ts = td.get("_ts") or td.get("time", 0)
                    if not ts or ts <= _last_aggregator_feed_ts.get(tkr, 0):
                        continue
                    cp = td.get("current_price") or td.get("current", 0)
                    if cp > 0:
                        candle_aggregator.process_tick(tkr, cp, td.get("volume", 0), tick_ts=ts)
                        _last_aggregator_feed_ts[tkr] = ts
            except Exception:
                pass
            await asyncio.sleep(2.0)
    bridge = asyncio.create_task(_poller_aggregator_bridge())
    bridge.add_done_callback(_log_task_error)

    # Periodic subscription consistency audit (every 60s) - ADR-011
    # Repairs missing subscriptions immediately (subscribe-only).
    async def _subscription_audit():
        while True:
            await asyncio.sleep(60)
            try:
                if viewed_ticker_mgr:
                    viewed_ticker_mgr.audit_and_repair()
            except Exception as e:
                print(f"[SubscriptionAudit] Error: {e}")
    audit_task = asyncio.create_task(_subscription_audit())
    audit_task.add_done_callback(_log_task_error)

    # Periodic sync of yfinance inactive symbols to DB (every 5 minutes)
    async def _yfinance_inactive_sync():
        while True:
            await asyncio.sleep(300)
            try:
                inactive = yf_downloader.get_inactive_symbols()
                if inactive:
                    from database import SessionLocal
                    db_sync = SessionLocal()
                    try:
                        for ticker, reason in inactive.items():
                            existing = db_sync.query(models.StockMetadata).filter(
                                models.StockMetadata.ticker == ticker
                            ).first()
                            if existing:
                                existing.is_active = False
                            else:
                                rec = models.StockMetadata(
                                    ticker=ticker, name=f"[{reason}]", exchange="NSE",
                                    is_active=False
                                )
                                db_sync.add(rec)
                        db_sync.commit()
                        yf_downloader.clear_inactive_symbols()
                        print(f"[YFSync] Synced {len(inactive)} inactive symbols to DB")
                    except Exception as e:
                        db_sync.rollback()
                        print(f"[YFSync] Error syncing inactive symbols: {e}")
                    finally:
                        db_sync.close()
            except Exception as e:
                print(f"[YFSync] Error: {e}")
    yf_sync_task = asyncio.create_task(_yfinance_inactive_sync())
    yf_sync_task.add_done_callback(_log_task_error)

    # ── Daily market-close sync: at ~18:30 IST, fetch today's daily candle for all active tickers ──
    async def _daily_market_close_sync():
        """After market close (~18:30 IST), ensure every watched ticker has today's daily candle."""
        import yfinance as yf, pandas as pd, math
        from sqlalchemy import text as sa_text

        while True:
            now_ist = database.get_ist_now()
            target_hour, target_min = 18, 30
            next_run = now_ist.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
            if now_ist >= next_run:
                next_run += timedelta(days=1)
            delay = (next_run - now_ist).total_seconds()
            print(f"[DailySync] Next sync at {next_run.time()} IST (in {delay/3600:.1f}h)")
            await asyncio.sleep(delay)

            # Time to sync - skip weekends and holidays
            sync_now = database.get_ist_now()
            if sync_now.weekday() >= 5:
                print(f"[DailySync] Weekend, skipping")
                continue
            with _holidays_lock:
                if sync_now.date() in NSE_HOLIDAYS:
                    print(f"[DailySync] Holiday, skipping")
                    continue

            print(f"[DailySync] Starting daily sync...")
            db_sync = SessionLocal()
            try:
                rows = db_sync.execute(
                    sa_text("SELECT DISTINCT ticker FROM candles WHERE timeframe='1D'")
                ).fetchall()
                watched = {r[0] for r in rows}
                today = sync_now.date()

                # Check which watched tickers already have today's candle in candles(1D)
                existing = db_sync.execute(
                    sa_text("SELECT DISTINCT ticker FROM candles WHERE timeframe='1D' AND timestamp::date=:d"),
                    {"d": today}
                ).fetchall()
                have_today = {r[0] for r in existing}
                missing = sorted(watched - have_today)

                if not missing:
                    print(f"[DailySync] All {len(watched)} watched tickers already have today's candle")
                    continue

                # Filter out known-failed/invalid tickers
                filtered = []
                skipped = 0
                for tkr in missing:
                    if _is_yfinance_failed(tkr):
                        skipped += 1
                        continue
                    is_valid, _ = yf_downloader.validate_symbol(tkr)
                    if not is_valid:
                        _mark_yfinance_failed(tkr)
                        skipped += 1
                        continue
                    filtered.append(tkr)
                missing = filtered
                if skipped:
                    print(f"[DailySync] Skipped {skipped} failed tickers, fetching {len(missing)}...")
                if not missing:
                    print(f"[DailySync] All tickers already have today's candle (after filtering)")
                    continue
                print(f"[DailySync] Fetching today's candle for {len(missing)} tickers...")
                loop = asyncio.get_event_loop()
                def _run_sync(missing_list, today_date):
                    local_db = SessionLocal()
                    o, f = 0, 0
                    try:
                        for tkr in missing_list:
                            try:
                                df = _yf_bg_download(_yfinance_ticker(tkr), period='5d', interval='1d', timeout=8)
                                if df is not None and not df.empty:
                                    if isinstance(df.columns, pd.MultiIndex):
                                        df.columns = df.columns.get_level_values(0)
                                    last_row = df.iloc[-1]
                                    ts = last_row.name
                                    if hasattr(ts, 'to_pydatetime'):
                                        ts = ts.to_pydatetime()
                                    ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
                                    if ts_naive.date() == today_date:
                                        o_val = float(last_row.get('Open', 0) or 0)
                                        h_val = float(last_row.get('High', 0) or 0)
                                        l_val = float(last_row.get('Low', 0) or 0)
                                        c_val = float(last_row.get('Close', 0) or 0)
                                        v_val = int(last_row.get('Volume', 0) or 0)
                                        if o_val > 0 and h_val > 0 and l_val > 0 and c_val > 0:
                                            h_val = max(o_val, h_val, c_val); l_val = min(o_val, l_val, c_val)
                                            local_db.execute(sa_text("""
                                                INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
                                                VALUES (:t, '1D', :ts, :o, :h, :l, :c, :v, true, 'YFINANCE', false)
                                                ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
                                            """), {"t": tkr, "ts": ts_naive, "o": o_val, "h": h_val, "l": l_val, "c": c_val, "v": v_val})
                                            local_db.commit()
                                            o += 1
                                            continue
                                    _mark_yfinance_failed(tkr)
                                    f += 1
                                else:
                                    _mark_yfinance_failed(tkr)
                                    f += 1
                            except Exception:
                                _mark_yfinance_failed(tkr)
                                f += 1
                            time.sleep(0.3)
                    finally:
                        local_db.close()
                    return o, f
                ok, fail = await loop.run_in_executor(_bg_executor, _run_sync, missing, today)
                print(f"[DailySync] Done: {ok} added, {fail} skipped/failed")
            except Exception as e:
                print(f"[DailySync] Error: {e}")
            finally:
                db_sync.close()
    daily_sync = asyncio.create_task(_daily_market_close_sync())
    daily_sync.add_done_callback(_log_task_error)

    # ── Rolling retention compression: compress aged 1D → 1W and 1W → 1M ──────
    # Runs nightly at 19:30 IST (after daily sync at 18:30).
    # As 1D data crosses the 2yr boundary it gets aggregated into 1W candles.
    # As 1W data crosses the 5yr boundary it gets aggregated into 1M candles.
    # ON CONFLICT DO NOTHING makes this idempotent — safe to run repeatedly.
    async def _retention_compress():
        from sqlalchemy import text as _sa_text
        from datetime import date as _date
        while True:
            _now = database.get_ist_now()
            _next = _now.replace(hour=19, minute=30, second=0, microsecond=0)
            if _now >= _next:
                _next += timedelta(days=1)
            await asyncio.sleep((_next - _now).total_seconds())

            _today = database.get_ist_now().date()
            _2yr = _date(_today.year - 2, _today.month, _today.day)
            _5yr = _date(_today.year - 5, _today.month, _today.day)
            try:
                _db = SessionLocal()
                try:
                    # 1D → 1W: candles older than 2yr
                    r1w = _db.execute(_sa_text("""
                        INSERT INTO candles
                            (ticker, timeframe, timestamp, open, high, low, close, volume,
                             is_completed, is_backfilled, data_source)
                        SELECT ticker, '1W',
                               DATE_TRUNC('week', timestamp)::timestamp,
                               (ARRAY_AGG(open  ORDER BY timestamp ASC))[1],
                               MAX(high), MIN(low),
                               (ARRAY_AGG(close ORDER BY timestamp DESC))[1],
                               SUM(volume)::bigint,
                               TRUE, TRUE, 'SD_AGG'
                        FROM candles
                        WHERE timeframe = '1D'
                          AND timestamp::date < :cutoff2
                          AND open > 0
                        GROUP BY ticker, DATE_TRUNC('week', timestamp)
                        ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
                    """), {"cutoff2": _2yr})
                    _db.commit()
                    # 1W → 1M: weekly candles older than 5yr
                    r1m = _db.execute(_sa_text("""
                        INSERT INTO candles
                            (ticker, timeframe, timestamp, open, high, low, close, volume,
                             is_completed, is_backfilled, data_source)
                        SELECT ticker, '1M',
                               DATE_TRUNC('month', timestamp)::timestamp,
                               (ARRAY_AGG(open  ORDER BY timestamp ASC))[1],
                               MAX(high), MIN(low),
                               (ARRAY_AGG(close ORDER BY timestamp DESC))[1],
                               SUM(volume)::bigint,
                               TRUE, TRUE, 'SD_AGG'
                        FROM candles
                        WHERE timeframe = '1W'
                          AND timestamp::date < :cutoff5
                          AND open > 0
                        GROUP BY ticker, DATE_TRUNC('month', timestamp)
                        ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
                    """), {"cutoff5": _5yr})
                    _db.commit()
                    if r1w.rowcount or r1m.rowcount:
                        print(f"[RetentionCompress] +{r1w.rowcount} 1W, +{r1m.rowcount} 1M candles compressed")
                finally:
                    _db.close()
            except Exception as _e:
                print(f"[RetentionCompress] Error: {_e}")

    retention_task = asyncio.create_task(_retention_compress())
    retention_task.add_done_callback(_log_task_error)

    # ── Pre-warm yfinance cache at market open ──
    async def _yfinance_prewarm():
        """At ~09:00 IST (or on startup if market about to open / already open),
        pre-fill the in-memory yfinance cache for all watched tickers.
        This ensures chart loads are instant for the first viewer of the day.
        Only runs once per day — never repeats mid-session."""
        from database import SessionLocal
        from sqlalchemy import text as sa_text
        last_run_date = None
        await asyncio.sleep(10)  # let other startup tasks finish
        while True:
            now_ist = database.get_ist_now()
            # Skip if already ran today
            if last_run_date == now_ist.date():
                await asyncio.sleep(3600)
                continue
            target_hour, target_min = 9, 0
            next_run = now_ist.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
            if now_ist >= next_run:
                next_run += timedelta(days=1)
            delay = (next_run - now_ist).total_seconds()
            # On startup, if market is open or about to open, run immediately
            if now_ist.weekday() < 5:
                market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
                market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
                if market_open <= now_ist <= market_close:
                    print(f"[PreWarm] Market is open, starting pre-warm immediately")
                elif market_open - now_ist <= timedelta(hours=1) and now_ist < market_open:
                    print(f"[PreWarm] Market opening in {(market_open - now_ist).seconds//60}min, starting pre-warm")
                else:
                    print(f"[PreWarm] Next pre-warm at {next_run.time()} IST (in {delay/3600:.1f}h)")
                    await asyncio.sleep(delay)
                    continue
            else:
                await asyncio.sleep(delay)
                continue

            # Skip weekends/holidays
            sync_now = database.get_ist_now()
            if sync_now.weekday() >= 5:
                continue
            with _holidays_lock:
                if sync_now.date() in NSE_HOLIDAYS:
                    print(f"[PreWarm] Holiday, skipping")
                    continue

            print(f"[PreWarm] Starting pre-warm for all watched tickers...")
            db_pw = SessionLocal()
            try:
                rows = db_pw.execute(
                    sa_text("SELECT DISTINCT ticker FROM stock_metadata WHERE is_active = TRUE AND exchange = 'NSE'")
                ).fetchall()
                watched_tickers = sorted({r[0] for r in rows})

                import concurrent.futures
                def _pw_worker(tkr):
                    try:
                        _fetch_yfinance_intraday(None, tkr, "5m", use_bg_semaphore=True)
                    except Exception:
                        pass

                loop = asyncio.get_event_loop()
                def _run_prewarm():
                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                        pool.map(_pw_worker, watched_tickers)
                await loop.run_in_executor(_bg_executor, _run_prewarm)
                last_run_date = sync_now.date()
                print(f"[PreWarm] Completed for {len(watched_tickers)} tickers")
            except Exception as e:
                print(f"[PreWarm] Error: {e}")
            finally:
                db_pw.close()

    prewarm_task = asyncio.create_task(_yfinance_prewarm())
    prewarm_task.add_done_callback(_log_task_error)

    # ── Startup: pre-populate yfinance failure cache for tickers with no recent data ──
    async def _prepopulate_failure_cache():
        await asyncio.sleep(5)
        try:
            from database import SessionLocal
            from sqlalchemy import text as sa_text
            db_fc = SessionLocal()
            try:
                cutoff = database.get_ist_now() - timedelta(days=45)
                rows = db_fc.execute(sa_text(
                    "SELECT DISTINCT ticker FROM stock_metadata WHERE ticker NOT IN "
                    "(SELECT DISTINCT ticker FROM candles WHERE timeframe='5m' AND timestamp >= :cutoff)",
                ), {"cutoff": cutoff}).fetchall()
                stale = [r[0] for r in rows]
                for t in stale:
                    yf_downloader._mark_failed(t, YFErrorClass.NOT_FOUND)
                if stale:
                    print(f"[Startup] Pre-marked {len(stale)} tickers as inactive (no unified 5m candles in 45 days)")
            finally:
                db_fc.close()
        except Exception as e:
            print(f"[Startup] Failure cache pre-population error: {e}")
    asyncio.create_task(_prepopulate_failure_cache())

    # ── Retention scheduler: run-once per NSE trading day, market-closed only ──
    def _retention_due_today() -> bool:
        """True when today is an NSE trading day AND today's retention cycle
        has not yet completed (fewer than all configured policy tiers —
        currently 6, spanning both the intraday and daily+ chains —
        terminal today). Tier count is read from the live policy below,
        not hardcoded, so this stays correct as rules are added/removed."""
        from database import SessionLocal
        from sqlalchemy import text as sa_text
        from exchange_calendar import nse_calendar
        from config import load_retention_policy

        today_ist = get_ist_now().date()       # NSE calendar decision
        if not nse_calendar.is_trading_day(today_ist):  # weekends/holidays -> never due
            return False

        today_local = database.get_ist_now().date()     # same basis as job start_time (engine uses datetime.now())
        tiers = [f"{r['source_tf']}_to_{r['target_tf']}"
                 for r in load_retention_policy()["retention_policy"]]
        with SessionLocal() as db:
            done = db.execute(
                sa_text("SELECT COUNT(*) FROM retention_jobs "
                        "WHERE start_time::date = :d AND status IN ('COMPLETED','SKIPPED') "
                        "AND job_type IN :tiers"),
                {"d": today_local, "tiers": tuple(tiers)},
            ).scalar()
        return (done or 0) < len(tiers)

    async def _retention_scheduler():
        """Poll loop: run the cycle ONCE per trading day, only while NSE closed.
        Idempotent across restarts (job journal in retention_jobs)."""
        while True:
            await asyncio.sleep(60)          # cheap poll (one lightweight query when idle)
            try:
                from aggregator import is_market_hour
                now = database.get_ist_now()
                if is_market_hour(now):
                    continue                 # never run while market open / close grace
                if not _retention_due_today():
                    continue                 # not a trading day, or already done today
                if _retention_service is not None:
                    print(f"[Retention] Due at {now.isoformat()} IST -> running cycle")
                    # DB-04: run_cycle() does real DB work (downsampling
                    # across every configured tier) and was blocking the
                    # shared event loop for its full duration -- market is
                    # closed while this runs, but users still browse
                    # portfolio/history pages then, and they'd all stall.
                    await asyncio.to_thread(_retention_service.run_cycle)
                else:
                    print("[Retention] Service not initialized, skipping")
            except Exception as e:
                print(f"[Retention] Scheduler check failed: {e}")

    retention_task = asyncio.create_task(_retention_scheduler())
    retention_task.add_done_callback(_log_task_error)

    # ── Daily pre-fill: ensure ALL 5,543 tickers have daily intraday_candles_5min ──
    async def _daily_prefill_all():
        """Run once on startup, then daily at 19:00 IST. Fetches 1D intraday_candles_5min
        from yfinance for all tickers that don't have today's candle yet.
        This eliminates yfinance dependency for daily history charts."""
        from database import SessionLocal
        from sqlalchemy import text as sa_text
        await asyncio.sleep(30)  # delay on first run
        last_run_date = None
        while True:
            now_ist = database.get_ist_now()
            # Only run once per day, after market close
            target_hour, target_min = 19, 0
            next_run = now_ist.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
            if now_ist >= next_run:
                next_run += timedelta(days=1)
            delay = (next_run - now_ist).total_seconds()
            # On startup (first run), run immediately regardless of time
            if last_run_date is not None:
                await asyncio.sleep(delay)
            # Skip if already ran today
            today = database.get_ist_now().date()
            if last_run_date == today:
                await asyncio.sleep(3600)
                continue

            print(f"[DailyPreFill] Checking all tickers for daily intraday_candles_5min...")
            db_df = SessionLocal()
            try:
                before = time.time()
                # Get all tickers
                all_tickers = [r[0] for r in db_df.execute(
                    sa_text("SELECT ticker FROM stock_metadata")
                ).fetchall()]
                # Find tickers that have enough recent 1D coverage (>=15 distinct dates in last 30 days).
                # Anything below that threshold is treated as missing and gets a 3-month backfill.
                threshold_30 = today - timedelta(days=30)
                have_recent = {r[0] for r in db_df.execute(
                    sa_text("""
                        SELECT ticker FROM candles
                        WHERE timeframe='1D' AND timestamp::date >= :t
                        GROUP BY ticker HAVING COUNT(DISTINCT timestamp::date) >= 15
                    """),
                    {"t": threshold_30}
                ).fetchall()}
                missing = [t for t in all_tickers if t not in have_recent]
                if not missing:
                    print(f"[DailyPreFill] All {len(all_tickers)} tickers already have today's daily candle")
                    last_run_date = today
                    continue
                # Filter out known-failed tickers (delisted/invalid) to avoid slow timeouts
                filtered = []
                skipped_failed = 0
                for t in missing:
                    if _is_yfinance_failed(t):
                        skipped_failed += 1
                        continue
                    is_valid, _ = yf_downloader.validate_symbol(t)
                    if not is_valid:
                        _mark_yfinance_failed(t)
                        skipped_failed += 1
                        continue
                    filtered.append(t)
                missing = filtered
                if skipped_failed:
                    print(f"[DailyPreFill] Skipped {skipped_failed} known-failed tickers, fetching {len(missing)}...")
                else:
                    print(f"[DailyPreFill] Fetching daily intraday_candles_5min for {len(missing)} tickers...")
                if not missing:
                    last_run_date = today
                    continue
                import concurrent.futures, yfinance as yf, pandas as pd
                def _fetch_daily(tkr):
                    # Fetch 3 months so any gap (e.g. backend offline for days/weeks)
                    # is backfilled in one shot, not just today's candle.
                    local_db = None
                    try:
                        df = _yf_bg_download(_yfinance_ticker(tkr), period="1y", interval="1d", timeout=12)
                        if df is None or df.empty:
                            _mark_yfinance_failed(tkr)
                            return False
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        rows = []
                        for idx, row in df.iterrows():
                            ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                            ts_naive = ts.replace(tzinfo=None) if (hasattr(ts, 'tzinfo') and ts.tzinfo) else ts
                            o = float(row.get('Open', 0) or 0)
                            h = float(row.get('High', 0) or 0)
                            l = float(row.get('Low', 0) or 0)
                            c = float(row.get('Close', 0) or 0)
                            v = int(row.get('Volume', 0) or 0)
                            if o > 0 and h > 0 and l > 0 and c > 0:
                                rows.append({"t": tkr, "ts": ts_naive,
                                             "o": o, "h": max(o, h, c), "l": min(o, l, c),
                                             "c": c, "v": v})
                        if rows:
                            local_db = SessionLocal()
                            for p in rows:
                                local_db.execute(sa_text("""
                                    INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
                                    VALUES (:t, '1D', :ts, :o, :h, :l, :c, :v, true, 'YFINANCE', false)
                                    ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
                                """), p)
                            local_db.commit()
                            return True
                        return False
                    except Exception:
                        _mark_yfinance_failed(tkr)
                        if local_db is not None:
                            local_db.rollback()
                        return False
                    finally:
                        if local_db is not None:
                            local_db.close()
                loop = asyncio.get_event_loop()
                def _run_prefill():
                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                        return list(pool.map(_fetch_daily, missing))
                results = await loop.run_in_executor(_bg_executor, _run_prefill)
                ok = sum(1 for r in results if r)
                fail = len(missing) - ok
                last_run_date = today
                elapsed = time.time() - before
                print(f"[DailyPreFill] Done: {ok} added, {fail} failed in {elapsed:.1f}s")
            except Exception as e:
                print(f"[DailyPreFill] Error: {e}")
            finally:
                db_df.close()
    prefill_task = asyncio.create_task(_daily_prefill_all())
    prefill_task.add_done_callback(_log_task_error)

    # ── 5m intraday prefill: fill missed candles once per market close ──────────
    # Runs at/after 16:30 IST each NSE trading day (1 hour after close grace).
    # Idempotent via ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING so a
    # restart mid-run safely re-runs without duplicating any existing row.
    async def _intraday_prefill_scheduler():
        while True:
            await asyncio.sleep(60)
            try:
                from aggregator import is_market_hour
                from intraday_prefill import prefill_due_today, run_prefill_session
                now = database.get_ist_now()
                if is_market_hour(now):
                    continue  # never run during market hours or close grace
                # Wait until 16:30 IST before the first daily run
                if now.hour < 16 or (now.hour == 16 and now.minute < 30):
                    continue
                if not prefill_due_today():
                    continue
                session_date = now.date()
                print(f"[IntradayPrefill] Due at {now.isoformat()} -> running {session_date}")
                await asyncio.to_thread(run_prefill_session, session_date)
            except Exception as _e:
                print(f"[IntradayPrefill] Scheduler error: {_e}")

    intraday_prefill_task = asyncio.create_task(_intraday_prefill_scheduler())
    intraday_prefill_task.add_done_callback(_log_task_error)

    # ── Startup recovery pipeline (runs once per boot, fully in background) ──────
    # Step 1 — Phantom cleanup:   delete any 15:30 IST candles (snap bug artifact)
    # Step 2 — Historical backfill: fill the Jul 6 → (today-7d) gap if >100 tickers
    #           are still missing 5m data (skips automatically once complete)
    # Step 3 — 7-day catchup:     fill any trading days missed during recent downtime
    async def _intraday_prefill_catchup():
        await asyncio.sleep(30)   # let AngelOne login and instrument load settle
        try:
            from intraday_prefill import (
                cleanup_phantom_slot_candles,
                needs_historical_backfill,
                run_historical_backfill,
                catchup_missed_sessions,
            )
            # Step 1: fast — delete phantom 15:30 candles (idempotent)
            await asyncio.to_thread(cleanup_phantom_slot_candles)
            # Step 2: heavy — fill the Jul-Aug ingestion gap (skips if already done)
            if await asyncio.to_thread(needs_historical_backfill):
                await asyncio.to_thread(run_historical_backfill)
            # Step 3: fill any days missed during recent downtime (last 7 trading days)
            await asyncio.to_thread(catchup_missed_sessions, 7)
        except Exception as _e:
            print(f"[IntradayPrefill] Startup recovery error: {_e}")

    catchup_task = asyncio.create_task(_intraday_prefill_catchup())
    catchup_task.add_done_callback(_log_task_error)

    # ── 1W / 1M tier backfill ─────────────────────────────────────────────────
    # Runs 90s after boot (after the 5m prefill starts).
    # Phase 1 (SQL aggregation from stock_data) is fast and always runs.
    # Phase 2 (AngelOne fetch for tickers still missing coverage) only runs
    # when needs_1w_backfill() / needs_1m_backfill() return True — skips
    # automatically once coverage is complete.
    async def _weekly_monthly_backfill():
        await asyncio.sleep(90)
        try:
            from backfill_weekly_monthly import run_startup_backfill
            await asyncio.to_thread(run_startup_backfill)
        except Exception as _e:
            print(f"[1W/1M Backfill] Startup error: {_e}")

    wm_task = asyncio.create_task(_weekly_monthly_backfill())
    wm_task.add_done_callback(_log_task_error)

    # ── TP/SL Execution Engine ─────────────────────────────────────────────────
    # Polls all PENDING orders every 2 s during market hours (09:15–15:30 IST)
    # and closes positions when TP or SL trigger prices are hit.
    # Must start AFTER all other startup tasks so AngelOne WS + aggregator are live.
    from execution_engine import start_execution_engine
    engine_task = asyncio.create_task(start_execution_engine())
    engine_task.add_done_callback(_log_task_error)
    print("[Startup] TP/SL Execution Engine wired — monitoring PENDING orders every 2s")

@app.on_event("shutdown")
async def shutdown_flush():
    """Flush all forming intraday_candles_5min to DB on graceful shutdown."""
    print("[Shutdown] Flushing forming intraday_candles_5min...")
    candle_aggregator.flush_all_forming()
    print("[Shutdown] Flush complete")

import atexit
atexit.register(lambda: candle_aggregator.flush_all_forming())

# ==================== NEW SERVICE INITIALIZATION ====================

_recovery_service = None
_retention_service = None
_chart_service = None
_validation_service = None


def init_recovery_service():
    global _recovery_service, _chart_service, _retention_service, _validation_service
    from database import SessionLocal
    _recovery_service = RecoveryService(
        db_session_factory=SessionLocal,
        historical_service=historical_service,
        yf_downloader=yf_downloader,
    )
    _retention_service = RetentionService(
        db_session_factory=SessionLocal,
        resample_svc=CandleResampler,
    )
    _chart_service = ChartService(
        live_mgr=live_timeframe_manager,
        cache=candle_cache,
        resample_svc=CandleResampler,
        db_session_factory=SessionLocal,
        candle_aggregator=candle_aggregator,
    )
    _validation_service = ValidationService(
        db_session_factory=SessionLocal,
        resample_svc=CandleResampler,
        exchange_calendar=nse_calendar,
    )
    candle_cache._live_mgr = live_timeframe_manager

    monitor.register("aggregator", lambda: monitor.get_aggregator_health(candle_aggregator))
    monitor.register("recovery", lambda: monitor.get_recovery_health(_recovery_service))
    monitor.register("timeframe_manager", lambda: monitor.get_timeframe_manager_health(live_timeframe_manager))
    monitor.register("cache", lambda: monitor.get_cache_health(candle_cache))
    monitor.register("retention", lambda: monitor.get_retention_health(_retention_service))
    monitor.register("event_bus", lambda: monitor.get_event_bus_health())
    monitor.register("exchange_calendar", lambda: {"status": "healthy"})


# ==================== MONITORING ENDPOINT ====================

@app.get("/api/monitoring/candle-system")
def get_candle_system_health():
    """Health status for all candle system components."""
    return monitor.get_health()


# ==================== VALIDATION ENDPOINT ====================

@app.get("/api/validation/run")
def run_validation(ticker: str = Query(...), timeframe: str = Query("5m"),
                   db: Session = Depends(get_db)):
    """Run all validation checks for a given ticker/timeframe."""
    from models import Candle
    clean = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    intraday_candles_5min = db.query(Candle).filter(
        Candle.ticker == clean,
        Candle.timeframe == timeframe,
        Candle.is_completed == True,
    ).order_by(Candle.timestamp.desc()).limit(500).all()
    candle_dicts = [{
        "timestamp": c.timestamp,
        "open": c.open, "high": c.high, "low": c.low,
        "close": c.close, "volume": c.volume,
        "time": int(c.timestamp.timestamp()) if hasattr(c.timestamp, 'timestamp') else 0,
    } for c in intraday_candles_5min]
    results = _validation_service.run_all(candle_dicts, timeframe)
    return _validation_service.generate_report(results)


# ==================== WIRE EVENT BUS + RECOVERY HOOK ====================

@app.on_event("startup")
async def wire_event_bus_and_recovery():
    loop = asyncio.get_event_loop()
    event_bus.set_loop(loop)

    if viewed_ticker_mgr is not None:
        original_view = viewed_ticker_mgr.view

        def patched_view(ticker: str):
            original_view(ticker)
            if _recovery_service is not None and _recovery_service.needs_recovery(ticker):
                threading.Thread(
                    target=_recovery_service.recover_ticker,
                    args=(ticker, candle_aggregator),
                    daemon=True,
                ).start()

        viewed_ticker_mgr.view = patched_view
        print("[Recovery] Patched ViewedTickerManager.view with lazy recovery")

# ── Warm the market-news cache at startup so dashboard loads news instantly ──
@app.on_event("startup")
async def warmup_market_news_cache():
    async def _warm():
        await asyncio.sleep(4)  # let other startup tasks settle first
        try:
            await news_search("market", limit=20)
            print("[Startup] Market news cache warmed.")
        except Exception as e:
            print(f"[Startup] News warmup failed (non-fatal): {e}")
    asyncio.create_task(_warm())

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".html", ".js", ".css")) or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

