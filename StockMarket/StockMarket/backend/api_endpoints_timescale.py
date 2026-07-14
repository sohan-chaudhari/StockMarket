"""
New API endpoints for TimescaleDB integration
Add these to your main.py file
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel

# Import new models
from models_timescale import Ticker, IntradayCandle10m, Candle1h, Candle1d
from database import get_db


# ============================================================================
# Response Models (Pydantic)
# ============================================================================

class CandleResponse(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    
    class Config:
        from_attributes = True


class TickerResponse(BaseModel):
    ticker_id: int
    ticker: str
    name: Optional[str]
    exchange: str
    
    class Config:
        from_attributes = True


# ============================================================================
# API Router
# ============================================================================

timescale_router = APIRouter(prefix="/api/timescale", tags=["TimescaleDB"])


# ============================================================================
# Endpoints
# ============================================================================

@timescale_router.get("/tickers", response_model=List[TickerResponse])
async def get_all_tickers(
    exchange: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get all tickers from normalized tickers table
    
    Query params:
        - exchange: Filter by exchange (NSE, BSE, INDEX)
    """
    query = db.query(Ticker).filter(Ticker.is_active == True)
    
    if exchange:
        query = query.filter(Ticker.exchange == exchange.upper())
    
    tickers = query.order_by(Ticker.ticker).all()
    return tickers


@timescale_router.get("/ticker/{ticker_symbol}", response_model=TickerResponse)
async def get_ticker_info(
    ticker_symbol: str,
    db: Session = Depends(get_db)
):
    """Get ticker information by symbol"""
    ticker = db.query(Ticker).filter(Ticker.ticker == ticker_symbol.upper()).first()
    
    if not ticker:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker_symbol} not found")
    
    return ticker


@timescale_router.get("/candles/intraday", response_model=List[CandleResponse])
async def get_intraday_candles(
    ticker: str,
    interval: str = Query("10m", regex="^(10m|1h|1d)$"),
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    days: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Get intraday candles for a ticker
    
    Path params:
        - ticker: Stock ticker symbol (e.g., RELIANCE.NS)
    
    Query params:
        - interval: 10m, 1h, or 1d (default: 10m)
        - from_date: Start date (ISO format)
        - to_date: End date (ISO format)
        - days: Number of days to fetch (alternative to from_date/to_date)
    
    Examples:
        /candles/intraday?ticker=RELIANCE.NS&interval=10m&days=7
        /candles/intraday?ticker=TCS.NS&interval=1h&from_date=2024-01-01&to_date=2024-01-31
    """
    # Get ticker_id
    ticker_obj = db.query(Ticker).filter(Ticker.ticker == ticker.upper()).first()
    
    if not ticker_obj:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
    
    # Determine date range
    if days:
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)
    elif not from_date or not to_date:
        # Default: last 7 days
        to_date = datetime.now()
        from_date = to_date - timedelta(days=7)
    
    # Query based on interval
    if interval == "10m":
        candles = db.query(IntradayCandle10m).filter(
            IntradayCandle10m.ticker_id == ticker_obj.ticker_id,
            IntradayCandle10m.time >= from_date,
            IntradayCandle10m.time <= to_date
        ).order_by(IntradayCandle10m.time).all()
        
        return [c.to_dict() for c in candles]
    
    elif interval == "1h":
        # Query continuous aggregate
        result = db.execute(text("""
            SELECT 
                bucket as time,
                open, high, low, close, volume
            FROM candles_1h
            WHERE ticker_id = :ticker_id
            AND bucket >= :from_date
            AND bucket <= :to_date
            ORDER BY bucket
        """), {
            "ticker_id": ticker_obj.ticker_id,
            "from_date": from_date,
            "to_date": to_date
        })
        
        candles = []
        for row in result:
            candles.append({
                'time': row.time.isoformat(),
                'open': float(row.open),
                'high': float(row.high),
                'low': float(row.low),
                'close': float(row.close),
                'volume': row.volume
            })
        
        return candles
    
    elif interval == "1d":
        # Query daily continuous aggregate
        result = db.execute(text("""
            SELECT 
                bucket as time,
                open, high, low, close, volume
            FROM candles_1d
            WHERE ticker_id = :ticker_id
            AND bucket >= :from_date
            AND bucket <= :to_date
            ORDER BY bucket
        """), {
            "ticker_id": ticker_obj.ticker_id,
            "from_date": from_date,
            "to_date": to_date
        })
        
        candles = []
        for row in result:
            candles.append({
                'time': row.time.isoformat(),
                'open': float(row.open),
                'high': float(row.high),
                'low': float(row.low),
                'close': float(row.close),
                'volume': row.volume
            })
        
        return candles


@timescale_router.get("/stats/compression")
async def get_compression_stats(db: Session = Depends(get_db)):
    """
    Get TimescaleDB compression statistics
    Shows how much space is being saved
    """
    result = db.execute(text("""
        SELECT 
            pg_size_pretty(SUM(before_compression_total_bytes)) as before,
            pg_size_pretty(SUM(after_compression_total_bytes)) as after,
            ROUND(
                100 - (SUM(after_compression_total_bytes)::FLOAT / 
                       NULLIF(SUM(before_compression_total_bytes), 0) * 100),
                2
            ) as compression_pct
        FROM timescaledb_information.compressed_chunk_stats
        WHERE hypertable_name = 'intraday_candles_10m'
    """))
    
    row = result.fetchone()
    
    if not row or not row.before:
        return {
            "message": "No compressed data yet",
            "before_size": "0 bytes",
            "after_size": "0 bytes",
            "compression_ratio": 0
        }
    
    return {
        "before_size": row.before,
        "after_size": row.after,
        "compression_ratio": float(row.compression_pct),
        "space_saved": f"{row.compression_pct}%"
    }


@timescale_router.get("/stats/candles")
async def get_candle_stats(db: Session = Depends(get_db)):
    """
    Get statistics about stored candles
    """
    result = db.execute(text("""
        SELECT 
            COUNT(*) as total_candles,
            COUNT(DISTINCT ticker_id) as unique_tickers,
            MIN(time) as oldest_candle,
            MAX(time) as newest_candle,
            pg_size_pretty(pg_total_relation_size('intraday_candles_10m')) as total_size
        FROM intraday_candles_10m
    """))
    
    row = result.fetchone()
    
    return {
        "total_candles": row.total_candles,
        "unique_tickers": row.unique_tickers,
        "oldest_candle": row.oldest_candle.isoformat() if row.oldest_candle else None,
        "newest_candle": row.newest_candle.isoformat() if row.newest_candle else None,
        "total_storage": row.total_size
    }


# ============================================================================
# Add to main.py
# ============================================================================

"""
# In your main.py, add this import:
from api_endpoints_timescale import timescale_router

# Then include the router:
app.include_router(timescale_router)

# Now you can access:
# GET /api/timescale/tickers
# GET /api/timescale/candles/intraday?ticker=RELIANCE.NS&interval=10m&days=7
# GET /api/timescale/stats/compression
# GET /api/timescale/stats/candles
"""
