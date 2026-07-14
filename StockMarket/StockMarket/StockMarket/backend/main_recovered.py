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
from routers import auth_router, trade_router

# Create generic stock_data table if not exists
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Stock Market API")

from fastapi.responses import Response
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon")



















































































































































































































































































































































































































































































































































































































































































































                        try:
                            for date_idx, row in hist_data.iterrows():
                                try:
                                    # Check existence to avoid PK error if we only want to fill gaps
                                    # simplified: catch integrity error per row or use merge
                                    new_record = models.StockData(
                                        ticker=ticker,
                                        date=date_idx.date(),
                                        open=float(row['Open']),
                                        high=float(row['High']),
                                        low=float(row['Low']),
                                        close=float(row['Close']),
                                        volume=int(row['Volume'])
                                    )
                                    db.add(new_record)
                                    db.commit() # Commit per row or batched
                                except Exception:
                                    db.rollback() # Ignore dupes
                            
                            print(f"DEBUG: Backfilled 5-day history for {ticker}")
                        except Exception as db_err:
                            print(f"DEBUG: Batch save error: {db_err}")





                            "ticker": ticker,
                            "date": last_row.name.date().isoformat(),
                            "open": open_p,
                            "high": float(last_row['High']),
                            "low": float(last_row['Low']),
                            "current_price": price,
                            "close": price,
                            "volume": int(last_row['Volume']),
                            "color": color,
                            "is_finalized": True
                        },
                        "timestamp": database.get_ist_now().isoformat()
                    }, ticker)
                except Exception as e:
                    print(f"Fallback fetch failed: {e}")
            return

        # OPTIMIZATION: Check if already finalized for today
        is_post_market = (now.hour == 15 and now.minute >= 35) or now.hour > 15

        existing_candle = db.query(models.CurrentDayCandle).filter(
            models.CurrentDayCandle.ticker == ticker,
            models.CurrentDayCandle.trading_date == today
        ).first()

        if existing_candle and existing_candle.is_finalized:
            # Finalized data found. Broadcast it and SKIP fetch.
            color = "green" if existing_candle.current_price >= existing_candle.open else "red"
            
            await manager.broadcast({
                "type": "candle_update",
                "data": {
                    "ticker": existing_candle.ticker,
                    "date": existing_candle.trading_date.isoformat(),
                    "open": existing_candle.open,
                    "high": existing_candle.high,
                    "low": existing_candle.low,
                    "current_price": existing_candle.current_price,
                    "close": existing_candle.close,
                    "volume": existing_candle.volume,
                    "color": color,
                    "is_finalized": True
                },
                "timestamp": database.get_ist_now().isoformat()
            }, ticker)
            return

        # 1. Fetch Live (Run in thread to avoid blocking event loop)
        scraped_data = await fetch_live_data(ticker)
        
        if scraped_data:
            print(f"YFINANCE LIVE [{ticker}]: {scraped_data}")
        else:
            print(f"Scraped {ticker}: None")
        
        if scraped_data is not None:
            # 2. Update DB & Candle Logic
            candle_data = update_intraday_candle_logic(db, ticker, scraped_data)
            
            # 3. Check Post-Market Finalization Logic (Fetch Once -> Finalize)
            if is_post_market:
                print(f"Post-market detected for {ticker}. Finalizing candle...")
                candle = db.query(models.CurrentDayCandle).filter(
                    models.CurrentDayCandle.ticker == ticker,
                    models.CurrentDayCandle.trading_date == today
               





















































































































































































































































































































































































































































































































































































































































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
        secure=False, # Set True in production (HTTPS)
        max_age=3600
    )
    # Return token in body (accessible by JS to send in header)
    return {"csrf_token": token}

async def validate_csrf(request: Request):
    """Dependency to validate CSRF token on specific routes."
























































    token = auth.generate_token()
    expires_at = datetime.now(IST) + timedelta(hours=24) # Use IST
    
    verification = models.VerificationToken(
        user_id=user.user_id,
        token_hash=token,
        otp_code=otp,
        token_type="email_verification",
        expires_at=expires_at
    )
    db.add(verification)
    db.commit()
    
    # Send verification email
    try:
        auth.send_verification_email(user.email, otp, user.full_name, token)
    except Exception as e:
        print(f"[AUTH] Failed to send verification email: {e}")
    
    print(f"[AUTH] User registered/updated: {user.email}")
    print(f"[AUTH] Verification OTP: {otp} | Token: {token}")
    
    return user


@app.post("/api/auth/verify-email", response_model=schemas.MessageResponse)
@limiter.limit("10/hour")
async def verify_email(request: Request, data: schemas.VerifyEmailRequest, db: Session = Depends(get_db)):
    """Verify email with OTP or token."""
    if not data.otp and not data.token:
        raise HTTPException(status_code=400, detail="OTP or token is required")
    
    # Find verification token
    if data.otp:
        verification = db.query(models.VerificationToken).filter(
            models.VerificationToken.otp_code == data.otp,
            models.VerificationToken.token_type == "email_verification",
            models.VerificationToken.expires_at > datetime.now(IST), # Use IST
            models.VerificationToken.used == False
        ).first()
    else:
        verification = db.query(models.VerificationToken).filter(
            models.VerificationToken.token_hash == data.token,
            models.VerificationToken.token_type == "email_verification",
            models.VerificationToken.expires_at > datetime.now(IST), # Use IST
            models.VerificationToken.used == False
        ).first()
    
    if not verification:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")
    
    # Mark user as verified
    user = db.query(models.User).filter(models.User.user_id == verification.user_id).first()
    if user:
        user.is_verified = True
        verification.used = True
        db.commit()
        
        # Send welcome email
        try:
            auth.send_welcome_email(user.email, user.full_name)
        except Exception as e:
            print(f"[AUTH] Failed to send welcome email: {e}")
            
        print(f"[AUTH] Email verified: {user.email}")
        return {"message": "Email verified successfully"}
    
    raise HTTPException(status_code=400, detail="User not found")


@app.post("/api/auth/resend-verification", response_model=schemas.MessageResponse)
@limiter.limit("3/hour")
async def resend_verification(request: Request, data: schemas.ResendVerificationRequest, db: Session = Depends(get_db)):
    """Resend verification OTP."""
    user = db.query(models.User).filter(models








































































































































































































































































































































































































































































































































































































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
    (18:00 IST) so it increments once per day — no DB state needed.
    """
    now_ist = datetime.now(IST)
    # daily_sync_scheduler fires at 18:00 IST.  Before 18:00 today the
    # "last sync" was yesterday at 18:00; after 18:00 it is today.
    if now_ist.hour >= 18:
        sync_date = now_ist.date()
    else:
        from datetime import timedelta as _td
        sync_date = (now_ist - _td(days=1)).date()
    return {"version": f"{sync_date}T18:00:00"}


@app.get("/api/all-stocks")
def get_all_stocks(db: Session = Depends(get_db)):
    stocks = db.query(models.StockMetadata).all()
    results = []
    for s in stocks:
        results.append({
            "ticker": s.ticker,
            "name": s.name,
            "logo": s.logo,
            "exchange": s.exchange,
            "basePrice": s.base_price







































































































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
    
    # Check if market has opened (9:15 AM IST)
    now = datetime.now(IST) # Use IST
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0).time()
    is_market_open_today = (
        today not in NSE_HOLIDAYS and 
        today.weekday() < 5 and 
        now.time() >= market_start
    )
    
    if is_market_open_today:
        live_candle = db.query(models.CurrentDayCandle).filter(
            models.CurrentDayCandle.ticker == ticker,
            models.CurrentDayCandle.trading_date == today
        ).first()
    
    # 3. Merge - Build response list
    response_list = list(history)
    
    # FIX: When market is open and we have a live candle, remove any stale StockData entry for today
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
            candles = historical_service.get_his


































































































































































































































































































































































































































































































































































































































































































    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

    position, message = TradingService.open_position(
        db=db,
        user_id=current_user.user_id,
        ticker=order.ticker,
        position_type=order.position_type.upper(),
        quantity=order.quantity,
        entry_price=order.entry_price,
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
        
        # 2. Connect
 




























































































































































                    current_prices[t] = prices_data[t]['current_price']
        except Exception as e:
            print(f"Price fetch error: {e}")

    result = []
    for pos in positions:
        orders = TradingService.get_position_orders(db, pos.id)
        current_price = current_prices.get(pos.ticker, pos.entry_price) # Fallback to entry
        
        # Calculate P&L
        if pos.position_type == 'LONG':
            pnl = (current_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - current_price) * pos.quantity

        result.append({
            "id": pos.id,
            "ticker": pos.ticker,
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


@app.get("/api/portfolio/closed-positions")
async def get_closed_positions(
    page: int = 1,
    limit: int = 20,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)

























































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


from fastapi.staticfiles import StaticFiles
import os

# Get absolute path to frontend directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# Mount frontend directory to serve HTML/CSS/JS (SPA Fallback)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


