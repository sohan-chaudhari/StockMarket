from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, Request, Response, BackgroundTasks

from pydantic import BaseModel

from fastapi.responses import JSONResponse

from starlette.middleware.base import BaseHTTPMiddleware

from sqlalchemy.orm import Session

from typing import List, Optional, Dict

from datetime import date, timedelta, datetime, timezone

IST = timezone(timedelta(hours=5, minutes=30))

import os

import asyncio

from fastapi.middleware.cors import CORSMiddleware

# Rate Limiting

from slowapi import Limiter, _rate_limit_exceeded_handler

from slowapi.util import get_remote_address

from slowapi.errors import RateLimitExceeded

import yfinance as yf

import pandas as pd

import requests

import httpx

from bs4 import BeautifulSoup

import json

import random

import secrets

from fetch_stocks import sync_market_data

from angelone_service import angelone_service

from historical_service import historical_service

from indicator_service import indicator_service

import models, schemas, database, auth
from database import get_db

from routers.auth_router import router as auth_router
from routers.trade_router import router as trade_router

# Create generic stock_data table if not exists

models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Stock Market API")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)}"})

from fastapi.responses import Response

@app.get("/favicon.ico", include_in_schema=False)

async def favicon():

    return Response(content=b"", media_type="image/x-icon")

# ==================== AUTH ENDPOINTS ====================

import auth

from slowapi import Limiter

from slowapi.util import get_remote_address

from slowapi.errors import RateLimitExceeded

from fastapi import Request

from fastapi.responses import JSONResponse

# Rate limiter

limiter = Limiter(key_func=get_remote_address)

@app.exception_handler(RateLimitExceeded)

async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."}
    )
# model.interval_minutes DOES NOT EXIST in models.py
existing = db.query(model).filter(
    model.ticker == ticker,
    model.timestamp == ts,
    model.interval_minutes == interval_mins  # ← crashes!
).first()
record = model(..., interval_minutes=interval_mins)  # ← crashes!
```

---

## 🟠 HIGH Severity Bugs

---

### BUG-06 — SMTP Email is Synchronous — Blocks the Event Loop
**File:** [`auth.py` L214–L236](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/auth.py#L214-L236)  
**Severity:** 🟠 High — freezes the server during registration/password reset

`send_email()` uses `smtplib.SMTP` (blocking I/O) inside an `async` FastAPI app. Every email send can take 1–5 seconds, blocking the entire event loop and freezing the API for all users during that time.

**Fix:** Wrap in `asyncio.get_event_loop().run_in_executor(None, send_email, ...)` or use `aiosmtplib`.

---

### BUG-07 — `get_watchlist` Orders by `UserWatchlist.created_at` — Column Doesn't Exist in Model
**File:** [`main.py` L1009](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/main.py#L1009)  
**Severity:** 🟠 High — watchlist endpoint crashes

```python
items = db.query(models.UserWatchlist).filter(...).order_by(models.UserWatchlist.created_at).all()
```
The `UserWatchlist` model only has `added_at` (not `created_at`). This throws an `AttributeError` at runtime, crashing the GET `/api/watchlist` endpoint for every user.

```python
# Fix:
.order_by(models.UserWatchlist.added_at)
```


    # Return token in body (accessible by JS to send in header)

    return {"csrf_token": token}


@app.get("/favicon.ico", include_in_schema=False)

async def favicon():

    return Response(content=b"", media_type="image/x-icon")

# ==================== AUTH ENDPOINTS ====================

# Rate limiter

limiter = Limiter(key_func=get_remote_address)

@app.exception_handler(RateLimitExceeded)

async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."}
    )


    ).first()

    if not item:

        raise HTTPException(status_code=404, detail=f"'{ticker}' not found in watchlist.")

    db.delete(item)

    db.commit()
### BUG-11 — JWT Secret Key Has Insecure Default
**File:** [`auth.py` L22](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/auth.py#L22)  
**Severity:** 🟠 High — security vulnerability

```python
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production-leverage-2026")
```
If the `.env` file is missing or `JWT_SECRET_KEY` is not set, the server uses a publicly known default key, making it trivial for an attacker to forge valid JWT tokens.

**Fix:** Raise an exception at startup if the key is not set: `SECRET_KEY = os.environ["JWT_SECRET_KEY"]`.

---

## 🟡 MEDIUM Severity Bugs

---

### BUG-12 — `validate_csrf` is Only Attached to `place_order` — Other State-Changing Endpoints Unprotected
**File:** [`main.py` L397 and L1176, L1188](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/main.py)  
**Severity:** 🟡 Medium — CSRF vulnerability

`validate_csrf` is only used as a dependency on `POST /api/trade/place-order`. The following state-changing endpoints have **no CSRF protection**:
- `POST /api/trade/close-position` (L1176)
- `POST /api/trade/set-limits` (L1188)
- `POST /api/watchlist/add` (L1013)
- `DELETE /api/watchlist/remove` (L158)

---

### BUG-13 — Duplicate Imports Throughout `main.py`
**File:** [`main.py`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/main.py)  
**Severity:** 🟡 Medium — code quality / confusion

Multiple modules are imported more than once:
- `auth` — imported at line 55 **and** line 83
- `Limiter` — imported at lines 25, 85
- `get_remote_address` — imported at lines 27, 87
- `RateLimitExceeded` — imported at lines 29, 89
- `Request` — imported at lines 1, 91
- `JSONResponse` — imported at lines 5, 93
- `Response` — imported at lines 5, 73

---

### BUG-14 — `TokenBlacklist` Table Grows Unbounded — No Cleanup
**File:** [`models.py` L164–L170](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/models.py#L164-L170)  
**Severity:** 🟡 Medium — performance degradation over time

The `token_blacklist` table accumulates every logged-out JWT forever. With 24-hour tokens, entries are useless after expiry but are never cleaned up. This will slow down `is_token_blacklisted()` queries over time.

**Fix:** Add a scheduled job or periodic cleanup that deletes rows older than `ACCESS_TOKEN_EXPIRE_HOURS`.

---

### BUG-15 — `StockData.volume` Column is `Integer` — Overflows for High-Volume Stocks
**File:** [`models.py` L17](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/models.py#L17)  
**Severity:** 🟡 Medium — data corruption

Indian large-cap stocks like RELIANCE, TCS can have volumes exceeding 2 billion on high-activity days. SQLAlchemy `Integer` maps to a 32-bit integer (max ~2.1 billion). Use `BigInteger` instead.

```python
# Current (can overflow):
volume = Column(Integer)

# Fix:
volume = Column(BigInteger)
```

---

### BUG-16 — `get_range_data`, `get_stock_data_history`, `get_stock_data_range` are Identical
**File:** [`main.py` L1235, L1241, L1247](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/main.py#L1235-L1251)  
**Severity:** 🟡 Medium — dead code / maintenance risk

Three separate API endpoints (`/api/range-data`, `/api/stock-data/history`, `/api/stock-data/range`) have **identical implementations** — they accept `start`/`end`/`resolution` parameters but **ignore them all** and always return all records. Any bug in one is duplicated across all three.

---

### BUG-17 — `get_ist_now()` Returns Timezone-Naive Datetime — Causes Comparison Bugs
**File:** [`database.py` L11](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/database.py#L11)

```python
def get_ist_now():
    return datetime.now(timezone(...)).replace(tzinfo=None)  # strips timezone!
```
The function strips timezone info before returning. This makes IST datetimes stored in the DB indistinguishable from UTC datetimes stored by other means, causing silent timezone confusion when comparing JWT expiry times or doing time arithmetic.

---

### BUG-18 — News Service and Main Backend Point to Same Port (`8000`)
**File:** [`main.py` L895](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/main.py#L895)  
**Severity:** 🟡 Medium — news proxy never works

```python
NEWS_SERVICE_URL = os.getenv("NEWS_SERVICE_URL", "http://127.0.0.1:8000/api")
```
The default points the news proxy back to **itself** (port 8000). The News Sentiment service runs separately (likely on a different port, e.g. 8001). Unless this env var is correctly configured, all news API calls proxy back to the main backend and fail.

---

## Summary Table

| # | Severity | File | Description |
|---|---|---|---|
| 01 | 🔴 Critical | `main.py` | `_safe_float` defined twice, second version is broken |
| 02 | 🔴 Critical | `main.py` | `set-limits` endpoint bypasses `TradingService`, no Orders created |
| 03 | 🔴 Critical | `trade_service.py` | Falsy check skips TP/SL=0.0 validation and order creation |
| 04 | 🔴 Critical | `main.py` | Closed positions show live P&L instead of realized P&L |
| 05 | 🔴 Critical | `main.py` | Backfill uses non-existent `interval_minutes` column → crashes |
| 06 | 🟠 High | `auth.py` | Blocking SMTP call freezes async event loop |
| 07 | 🟠 High | `main.py` | Watchlist orders by `created_at` (column is `added_at`) → crash |
| 08 | 🟠 High | `main.py` | Portfolio summary always returns `win_rate=0` |
| 09 | 🟠 High | `main.py` | Open positions always show `unrealized_pnl=0` |
| 10 | 🟠 High | `execution_engine.py` | Price engine uses stale candle data (no date filter) |
| 11 | 🟠 High | `auth.py` | Insecure JWT secret key default value |
| 12 | 🟡 Medium | `main.py` | CSRF only on place-order, missing on close/set-limits/watchlist |
| 13 | 🟡 Medium | `main.py` | Many duplicate imports |
| 14 | 🟡 Medium | `models.py` | `token_blacklist` table grows forever (no expiry cleanup) |
| 15 | 🟡 Medium | `models.py` | `StockData.volume` is `Integer` (should be `BigInteger`) |
| 16 | 🟡 Medium | `main.py` | 3 identical stock-data range endpoints with dead params |
| 17 | 🟡 Medium | `database.py` | `get_ist_now()` strips timezone info → silent UTC/IST confusion |
| 18 | 🟡 Medium | `main.py` | News proxy default URL points to itself (port 8000) |

    if live_candle:

        # Remove any existing entry for today from the list (it might be stale)

        response_list = [d for d in response_list if d.date != today]

        response_list.append(live_candle)

    return response_list

# Helper for Asynchronous Catch-up Backfill

def perform_on_demand_backfill(ticker: str, interval: str, backfill_start: datetime, now: datetime):

    """Background task to fetch missing intraday data without blocking the API."""

    db = database.SessionLocal()

    ticker = ticker.strip().upper()

    # 1. Lock the ticker for sequential processing

    backfill_locks.add(ticker)

    try:

        model = models.IntradayCandle5Min if interval == "5m" else models.IntradayCandle15Min

        # Ensure logged in

        if not historical_service.is_logged_in:

            historical_service.login()

        print(f"[Intraday] [BG] Fetching {ticker} from {backfill_start} to {now}...")

        # Try AngelOne first (unless it's an index which often lags)

        candles = []

        is_index = (ticker.upper() in ["NIFTY", "BANKNIFTY", "SENSEX", "NIFTY 50", "NIFTY BANK"])

        if not is_index:

            candles = historical_service.get_historical_candles(
                token=ticker,
                exchange="NSE",
                interval=interval,
                from_date=backfill_start.date(),
                to_date=now.date()
            ) or []

        if candles:
            for c in candles:
                ts = c.get("timestamp")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                if not ts:
                    continue
                # BUG-05 FIX: Query only by ticker+timestamp — interval_minutes column does not exist on these models
                existing = db.query(model).filter(
                    model.ticker == ticker,
                    model.timestamp == ts
                ).first()
                if not existing:
                    record = model(
                        ticker=ticker,
                        timestamp=ts,
                        open=c.get("open", 0),
                        high=c.get("high", 0),
                        low=c.get("low", 0),
                        close=c.get("close", 0),
                        volume=int(c.get("volume", 0))
                    )
                    db.add(record)
            db.commit()
            print(f"[Intraday] [BG] Saved {len(candles)} candles for {ticker}")

    except Exception as e:
        print(f"[Intraday] [BG] Error backfilling {ticker}: {e}")

    finally:

    finally:
        backfill_locks.discard(ticker)
        db.close()

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

    position, message = TradingService.open_position(

        db=db,

        user_id=current_user.user_id,

        ticker=order.ticker,

        position_type=order.position_type.upper(),

        quantity=order.quantity,

        entry_price=order.entry_price,

        take_profit=order.take_profit,

        take_profit=order.take_profit,

        stop_loss=order.stop_loss,

        stock_name=stock_name

    )

    if not position:

        raise HTTPException(status_code=400, detail=message)

    return {

        "message": message,

        "position_id": position.id,

        "balance": db.query(models.User).filter(models.User.user_id == current_user.user_id).first().virtual_balance

    }

# ==================== USER WEBSOCKET ====================

from websocket_manager import user_ws_manager

from fastapi import WebSocketDisconnect

@app.websocket("/ws/user")

async def user_websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):

    """

    WebSocket endpoint for real-time user updates (private).

    Client connects then sends auth token as first JSON message: {"token": "JWT_TOKEN"}

    """

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

        user = db.query(models.User).filter(models.User.user_id == user_id).first()

        if not user or not user.is_active:

             print(f"[WS] Auth failed: User not found or inactive")

             await websocket.send_json({"type": "error", "message": "Authentication failed"})

             await websocket.close(code=4001)

             return

        await websocket.send_json({"type": "authenticated"})

        await user_ws_manager.connect(websocket, user_id)
        try:
            while True:
                msg = await websocket.receive_json()
                await user_ws_manager.handle_message(user_id, msg)
        except WebSocketDisconnect:
            user_ws_manager.disconnect(user_id)

    except Exception as e:
        print(f"[WS User] Error: {e}")

@app.get("/api/portfolio/closed-positions")
async def get_closed_positions(
    page: int = 1, limit: int = 20,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    offset = (page - 1) * limit
    query = db.query(models.Position).filter(
    ).order_by(models.Position.closed_at.desc())
    total = query.count()
    positions = query.offset(offset).limit(limit).all()

    # BUG-04 FIX: Closed positions don't need live prices — use stored realized_pnl and closing_price

    result = []

    for pos in positions:

        orders = TradingService.get_position_orders(db, pos.id)
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
            "unrealized_pnl": realized_pnl,
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
            "stock_name": pos.stock_name,

            "position_type": pos.position_type,

            "quantity": pos.quantity,

            "entry_price": pos.entry_price,

            "current_price": current_price,

            "unrealized_pnl": pnl,

            "total_investment": pos.total_investment,

            "created_at": pos.created_at.isoformat(),

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
        import math
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

async def fetch_batch_live_data(tickers: List[str]) -> Dict[str, Dict]:
    prices = {}
    if not tickers:
        return prices
    yf_tickers = []
    INDEX_MAP = {
        "NIFTY":           "^NSEI",
        "SENSEX":          "^BSESN",
        "BANKNIFTY":       "^NSEBANK",
        "FINNIFTY":        "NIFTY_FIN_SERVICE.NS",
        "MIDCAP":          "^NSEMDCP50",
        "SMALLCAP":        "^NSESCP250",
        # NSE Sector indices
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
    for t in tickers:
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
        loop = asyncio.get_event_loop()
        def _fetch():
            return yf.download(
                tickers=yf_tickers,
                period="5d",
                interval="1d",
                progress=False,
                group_by="ticker"
            )
        df = await loop.run_in_executor(None, _fetch)
        if df is not None and not df.empty:
            _REVERSE_INDEX_MAP = {
                "NIFTY": "^NSEI",
                "SENSEX": "^BSESN",
                "BANKNIFTY": "^NSEBANK",
                "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
                "MIDCAP": "^NSEMDCP50",
                "SMALLCAP": "^NSESCP250",
                "NIFTY_AUTO": "^CNXAUTO",
                "NIFTY_IT": "^CNXIT",
                "NIFTY_PHARMA": "^CNXPHARMA",
                "NIFTY_FMCG": "^CNXFMCG",
                "NIFTY_METAL": "^CNXMETAL",
                "NIFTY_ENERGY": "^CNXENERGY",
                "NIFTY_MEDIA": "^CNXMEDIA",
                "NIFTY_PSU_BANK": "^CNXPSUBANK",
                "NIFTY_REALTY": "^CNXREALTY",
                "NIFTY_INFRA": "^CNXINFRA",
                "NIFTY_CPSE": "NIFTYCPSE.NS",
                "NIFTY_MNC": "NIFTYMNC.NS",
            }
            for t in tickers:
                raw = t.strip().upper()
                yf_key = _REVERSE_INDEX_MAP.get(raw)
                if yf_key is None:
                    if "FINNIFTY" in raw:
                        yf_key = "NIFTY_FIN_SERVICE.NS"
                    elif "." in raw:
                        yf_key = raw
                    else:
                        yf_key = f"{raw}.NS"
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        if yf_key in df.columns.get_level_values(0):
                            col = df[yf_key]
                        else:
                            continue
                    else:
                        col = df
                    
                    # Drop NA rows for this ticker so we get accurate last/prev rows
                    col = col.dropna(how='all')
                    
                    last_row = col.iloc[-1] if not col.empty else None
                    prev_row = col.iloc[-2] if len(col) > 1 else None
                    
                    if last_row is not None:
                        close_val = _safe_float(last_row.get("Close", last_row.get("close", 0)))
                        open_val = _safe_float(last_row.get("Open", last_row.get("open", 0)))
                        high_val = _safe_float(last_row.get("High", last_row.get("high", 0)))
                        low_val = _safe_float(last_row.get("Low", last_row.get("low", 0)))
                        vol = _safe_int(last_row.get("Volume", last_row.get("volume", 0)))
                        
                        prev_close_val = _safe_float(prev_row.get("Close", prev_row.get("close", 0))) if prev_row is not None else open_val
                        
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
                            "change": change,
                            "change_pct": change_pct,
                        }
                except Exception as e:
                    pass
    except Exception as e:
        print(f"[Live] fetch_batch error: {e}")
    return prices

@app.post("/api/live-prices")
async def get_live_prices_batch(req: BatchPriceRequest):
    prices = await fetch_batch_live_data(req.tickers)
    return prices

# ==================== MARKET MOVERS ====================

def is_market_open_now():
    now = datetime.now(IST)
    today = now.date()
    if today.weekday() >= 5:
        return False
    if today in NSE_HOLIDAYS:
        return False
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end

MOVER_TICKERS = ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN","BHARTIARTL","ITC","WIPRO","LT","HINDUNILVR","MARUTI","DRREDDY","HCLTECH","AXISBANK","BAJFINANCE","KOTAKBANK","TITAN","ASIANPAINT","NTPC","SUNPHARMA","ULTRACEMCO","ONGC","POWERGRID","M&M","TATAMOTORS","TATASTEEL","JSWSTEEL","ADANIPORTS","GRASIM","NIFTY","SENSEX","BANKNIFTY"]

STOCK_META = {}
try:
    meta_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "stocks_temp.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta_list = json.load(f)
        for s in meta_list:
            if s.get("ticker"):
        "gainers": gainers[:15], "losers": losers[:15], "most_active": most_active[:15],
        "market_open": is_market_open_now(),
        "last_updated": datetime.now(IST).isoformat(),
        "advance_count": len(gainers), "decline_count": len(losers)
    }

# ==================== NEWS & SENTIMENT ====================

@app.get("/api/scanx/news/market-sentiment")
async def proxy_market_sentiment(db: Session = Depends(get_db)):
    try:
        # Generate sentiment from actual market data
        today = date.today()
        indices = ['NIFTY', 'SENSEX', 'BANKNIFTY']
        scores = []
        for t in indices:
        "market_open": is_market_open_now(),
        "last_updated": datetime.now(IST).isoformat(),
        "advance_count": len(gainers), "decline_count": len(losers)
    }

# ==================== NEWS & SENTIMENT ====================

@app.get("/api/scanx/news/market-sentiment")
async def proxy_market_sentiment(db: Session = Depends(get_db)):
    try:
        # Generate sentiment from actual market data
        today = date.today()
        indices = ['NIFTY', 'SENSEX', 'BANKNIFTY']
        scores = []
        for t in indices:
            row = db.query(models.StockData).filter(
                models.StockData.ticker == t,
                models.StockData.date == today
            ).first()
            if row and row.open and row.close:
                chg_pct = ((row.close - row.open) / row.open) * 100
                scores.append(chg_pct)
        if scores:
            avg = sum(scores) / len(scores)
            if avg > 0.3:
                label, score = "positive", min(100, 50 + int(avg * 10))
            elif avg < -0.3:
                label, score = "negative", max(0, 50 + int(avg * 10))
            else:
                label, score = "neutral", 50
            summary = f"Markets are {label} today with average change of {avg:+.2f}% across major indices."
        else:
            # Fallback to last available trading day
            row = db.query(models.StockData).filter(
                models.StockData.ticker == 'NIFTY'
            ).order_by(models.StockData.date.desc()).first()
            if row and row.open and row.close:
                chg_pct = ((row.close - row.open) / row.open) * 100
                label = "positive" if chg_pct > 0.3 else ("negative" if chg_pct < -0.3 else "neutral")
                score = max(0, min(100, 50 + int(chg_pct * 10)))
                summary = f"Based on last trading day ({row.date}), market sentiment is {label}."
            else:
                label, score, summary = "neutral", 50, "Market data is being processed."
        return {"sentiment": label, "label": label, "score": score, "summary": summary}
    except Exception as e:
        print(f"[Sentiment] Error: {e}")
        return {"sentiment": "neutral", "label": "neutral", "score": 50, "summary": "Market sentiment temporarily unavailable", "error": str(e)}

@app.get("/api/news/general")
async def proxy_news_general(db: Session = Depends(get_db)):
    try:
        articles = []
        top_stocks = db.query(models.StockMetadata.ticker, models.StockMetadata.name).filter(
            models.StockMetadata.ticker.isnot(None)
        ).limit(10).all()
        ticker_list = [t for t, n in top_stocks]
        if not ticker_list:
            return {"articles": []}
        # Single batch query instead of N+1
        all_rows = db.query(models.StockData).filter(
            models.StockData.ticker.in_(ticker_list)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"News service returned {resp.status_code}", "articles": []}
    except Exception as e:
        print(f"[News Proxy] /api/news/ticker/{ticker} error: {e}")
        return {"error": str(e), "articles": []}

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
        self.active_connections.pop(client_id, None)
        self.user_topics.pop(client_id, None)

    async def send_personal_message(self, message: dict, client_id: str):
        ws = self.active_connections.get(client_id)
        if ws:
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
        self.active_connections.pop(client_id, None)
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
        disconnected = []
        for cid, ws in self.active_connections.items():
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"[WS Dashboard] Broadcast error to {cid}: {e}")
                disconnected.append(cid)
        for cid in disconnected:
            self.disconnect(cid)

manager = ConnectionManager()

@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    client_id = await manager.connect(websocket)
    try:
        manager.disconnect(client_id)

# ==================== WATCHLIST ENDPOINTS ====================

@app.get("/api/watchlist", response_model=schemas.WatchlistResponse)
def get_watchlist(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    items = db.query(models.UserWatchlist).filter(
        models.UserWatchlist.user_id == current_user.user_id
    ).order_by(models.UserWatchlist.created_at).all()
    tickers = [item.ticker for item in items]
    return schemas.WatchlistResponse(watchlist=tickers, count=len(tickers))

                    for t in topics:
                        manager.user_topics[client_id].discard(t)
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        print(f"[WS Dashboard] Error: {e}")
        manager.disconnect(client_id)

# ==================== WATCHLIST ENDPOINTS ====================

@app.get("/api/watchlist", response_model=schemas.WatchlistResponse)
def get_watchlist(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    items = db.query(models.UserWatchlist).filter(
        models.UserWatchlist.user_id == current_user.user_id
    # BUG-07 FIX: Column is `added_at`, not `created_at` — ordering by a non-existent column crashed this endpoint
    ).order_by(models.UserWatchlist.added_at).all()
    tickers = [item.ticker for item in items]
            elif msg_type == "subscribe":
                topics = data.get("topics", [])
                if client_id in manager.user_topics:
                    manager.user_topics[client_id].update(topics)
            elif msg_type == "unsubscribe":
                topics = data.get("topics", [])
                if client_id in manager.user_topics:
                    for t in topics:
                        manager.user_topics[client_id].discard(t)
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        print(f"[WS Dashboard] Error: {e}")
        manager.disconnect(client_id)

# ==================== WATCHLIST ENDPOINTS ====================

@app.get("/api/watchlist", response_model=schemas.WatchlistResponse)
def get_watchlist(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    items = db.query(models.UserWatchlist).filter(
        models.UserWatchlist.user_id == current_user.user_id
    # BUG-07 FIX: Column is `added_at`, not `created_at` — ordering by a non-existent column crashed this endpoint
    ).order_by(models.UserWatchlist.added_at).all()
    tickers = [item.ticker for item in items]
    return schemas.WatchlistResponse(watchlist=tickers, count=len(tickers))

@app.post("/api/watchlist/add", response_model=schemas.WatchlistAddResponse)
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

@app.get("/api/fii-dii")
async def get_fii_dii():
    """Fetch FII/DII data: tries NSE with session cookie first, then Moneycontrol scrape."""
    today_str = datetime.now(IST).strftime("%d-%m-%Y")

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
                return {"entries": entries, "source": "NSE India", "last_updated": datetime.now(IST).isoformat()}
    except Exception as e:
        print(f"[FII/DII] NSE attempt failed: {e}")

    # ── Attempt 2: Moneycontrol FII/DII scrape ──
    try:
        mc_url = "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php"
        mc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.moneycontrol.com/",
        }
        mc_resp = requests.get(mc_url, headers=mc_headers, timeout=10)
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
                    return {"entries": entries, "source": "Moneycontrol", "last_updated": datetime.now(IST).isoformat()}
    except Exception as e:
        print(f"[FII/DII] Moneycontrol attempt failed: {e}")

    # ── Fallback: return empty with message ──
    return {"entries": [], "source": "Unavailable", "last_updated": datetime.now(IST).isoformat(), "error": "FII/DII data temporarily unavailable. NSE blocks direct API access without browser session."}

# ==================== PORTFOLIO ENDPOINTS ====================

@app.get("/api/portfolio/open-positions")
def get_open_positions(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    positions = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id,
        models.Position.status == "OPEN"
    ).order_by(models.Position.created_at.desc()).all()
    result = []
    for pos in positions:
        orders = TradingService.get_position_orders(db, pos.id)
        result.append({
            "id": pos.id,
            "ticker": pos.ticker,
            "stock_name": pos.stock_name,
            "position_type": pos.position_type,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "current_price": pos.entry_price,
            "unrealized_pnl": 0,
            "total_investment": pos.total_investment,
            "created_at": pos.created_at.isoformat(),
            "tp_edit_count": pos.tp_edit_count,
            "sl_edit_count": pos.sl_edit_count,
            "take_profit": orders["TP"].trigger_price if orders.get("TP") else None,
            "stop_loss": orders["SL"].trigger_price if orders.get("SL") else None
        })
    return result

@app.get("/api/portfolio/summary")
def get_portfolio_summary(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    user = db.query(models.User).filter(models.User.user_id == current_user.user_id).first()
    balance = user.virtual_balance if user else 1000000.0
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
    return {
        "current_balance": balance,
        "total_invested": invested_sum,
        "total_realized_pnl": realized_pnl,
        "total_unrealized_pnl": 0,
        "total_positions": total_positions,
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "win_rate": 0,
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

@app.get("/api/portfolio/summary")
def get_portfolio_summary(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    user = db.query(models.User).filter(models.User.user_id == current_user.user_id).first()
    balance = user.virtual_balance if user else 1000000.0
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

    # BUG-08 FIX: Compute actual win/loss stats from CLOSED positions instead of hardcoding zeros
    pnl_rows = db.query(models.Position.realized_pnl).filter(
    """Return list of ticker variants to query, handling .NS/.BO suffixes."""
    t = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    variants = [t]
    if '.' not in t and t not in ['NIFTY', 'BANKNIFTY', 'SENSEX']:
        variants.append(f"{t}.NS")
    return variants

@app.get("/api/stock-data/intraday")
def get_intraday_data(ticker: str, interval: str = "5m", limit: int = 200, db: Session = Depends(get_db)):
        "current_balance": balance,
        "total_invested": invested_sum,
        "total_realized_pnl": realized_pnl,
        "total_unrealized_pnl": 0,
        "total_positions": total_positions,
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "win_rate": win_rate,
        "total_wins": total_wins,
        "total_losses": total_losses,
    }

@app.post("/api/trade/close-position")
def close_position(req: schemas.ClosePositionRequest, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    position, error_msg = TradingService.close_position(
        db=db, user_id=current_user.user_id,
        position_id=req.position_id,
        closing_price=req.closing_price,
        close_type="MANUAL"
    )
    if not position:
        raise HTTPException(status_code=400, detail=error_msg)
    return {"message": "Position closed", "position_id": position.id}

@app.post("/api/trade/set-limits")
def set_limits(req: schemas.SetLimitsRequest, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    # BUG-02 FIX: Delegate to TradingService so edit counts are tracked and pending Order rows are
    # properly cancelled/created. Old code directly set position.take_profit/stop_loss columns
    # but the execution engine reads Order rows — so TP/SL was never actually triggered.
    success, message = TradingService.set_limits(
        db=db,
        user_id=current_user.user_id,
        position_id=req.position_id,
        take_profit=req.take_profit,
        stop_loss=req.stop_loss
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    # Return updated TP/SL from Order rows for frontend compatibility
    orders = TradingService.get_position_orders(db, req.position_id)
    return {
        "message": message,
        "take_profit": orders["TP"].trigger_price if orders["TP"] else None,
        "stop_loss": orders["SL"].trigger_price if orders["SL"] else None
    }

# ==================== INTRA DAY / RANGE DATA ====================

def _normalize_ticker(ticker: str) -> list:
    """Return list of ticker variants to query, handling .NS/.BO suffixes."""
    t = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    variants = [t]
    if '.' not in t and t not in ['NIFTY', 'BANKNIFTY', 'SENSEX']:
        variants.append(f"{t}.NS")
    return variants

@app.get("/api/stock-data/intraday")
def get_intraday_data(ticker: str, interval: str = "5m", limit: int = 200, db: Session = Depends(get_db)):
    ticker = ticker.strip().upper()
    model = models.IntradayCandle5Min if interval == "5m" else models.IntradayCandle15Min
    candles = db.query(model).filter(
        model.ticker == ticker
    ).order_by(model.timestamp.desc()).limit(limit).all()
    candles.reverse()
    return [{
        "timestamp": c.timestamp.isoformat(),
        "open": _safe_float(c.open), "high": _safe_float(c.high), "low": _safe_float(c.low), "close": _safe_float(c.close), "volume": _safe_float(c.volume)
    } for c in candles]

@app.get("/api/range-data")
def get_range_data(ticker: str = Query(...), resolution: str = "1d", start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    db_tickers = _normalize_ticker(ticker)
    records = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers)).order_by(models.StockData.date.asc()).all()
    return [{"time": str(r.date), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_float(r.volume)} for r in records]

@app.get("/api/stock-data/history")
def get_stock_data_history(ticker: str = Query(...), db: Session = Depends(get_db)):
    db_tickers = _normalize_ticker(ticker)
    records = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers)).order_by(models.StockData.date.asc()).all()
    return [{"time": str(r.date), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_float(r.volume)} for r in records]

@app.get("/api/stock-data/range")
def get_stock_data_range(ticker: str = Query(...), resolution: str = "1d", start: Optional[str] = None, end: Optional[str] = None, db: Session = Depends(get_db)):
    db_tickers = _normalize_ticker(ticker)
    records = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers)).order_by(models.StockData.date.asc()).all()
    return [{"time": str(r.date), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_float(r.volume)} for r in records]


@app.get("/api/top-9-history")
async def get_top_9_history(db: Session = Depends(get_db)):
    top_tickers = db.query(models.StockMetadata.ticker, models.StockMetadata.name).limit(9).all()
    ticker_list = [t for t, n in top_tickers]
    result = {}
    if not ticker_list:
        return result
    # Single batch query instead of N+1
    all_data = db.query(models.StockData).filter(
        models.StockData.ticker.in_(ticker_list)
    ).order_by(models.StockData.ticker, models.StockData.date.desc()).all()
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

# ==================== STATIC FILES MOUNT ====================

from fastapi.staticfiles import StaticFiles
