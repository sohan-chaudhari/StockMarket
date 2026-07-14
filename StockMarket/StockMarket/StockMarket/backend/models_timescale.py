"""
New SQLAlchemy models for TimescaleDB integration
Add these to your existing models.py file
"""

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, SMALLINT, REAL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import TIMESTAMP
from datetime import datetime

Base = declarative_base()


# ============================================================================
# TimescaleDB Models
# ============================================================================

class Ticker(Base):
    """
    Normalized ticker lookup table
    Maps ticker symbols to SMALLINT IDs for 87% space savings
    """
    __tablename__ = "tickers"
    
    ticker_id = Column(SMALLINT, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, unique=True, index=True)
    name = Column(String(255))
    exchange = Column(String(10))  # 'NSE', 'BSE', 'INDEX'
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Ticker {self.ticker_id}: {self.ticker} ({self.exchange})>"


class IntradayCandle10m(Base):
    """
    10-minute intraday candles (TimescaleDB hypertable)
    Optimized data types: REAL (4 bytes) vs DOUBLE (8 bytes)
    
    Note: This table is converted to a hypertable via SQL migration
    """
    __tablename__ = "intraday_candles_10m"
    
    time = Column(TIMESTAMP(timezone=True), primary_key=True, nullable=False)
    ticker_id = Column(SMALLINT, ForeignKey('tickers.ticker_id'), primary_key=True, nullable=False)
    open = Column(REAL, nullable=False)   # 4 bytes (sufficient precision for stock prices)
    high = Column(REAL, nullable=False)
    low = Column(REAL, nullable=False)
    close = Column(REAL, nullable=False)
    volume = Column(Integer, nullable=False)  # 4 bytes (INTEGER vs BIGINT)
    
    def __repr__(self):
        return f"<Candle10m {self.ticker_id} @ {self.time}: {self.close}>"
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'time': self.time.isoformat(),
            'open': float(self.open),
            'high': float(self.high),
            'low': float(self.low),
            'close': float(self.close),
            'volume': self.volume
        }


# ============================================================================
# Helper Models for Continuous Aggregates (Read-only views)
# ============================================================================
# Note: These are materialized views, not actual tables
# Define them as read-only models for querying

class Candle1h(Base):
    """
    1-hour candles (continuous aggregate view)
    Auto-generated from intraday_candles_10m
    """
    __tablename__ = "candles_1h"
    __table_args__ = {'info': {'is_view': True}}
    
    bucket = Column(TIMESTAMP(timezone=True), primary_key=True)
    ticker_id = Column(SMALLINT, primary_key=True)
    open = Column(REAL)
    high = Column(REAL)
    low = Column(REAL)
    close = Column(REAL)
    volume = Column(Integer)
    
    def to_dict(self):
        return {
            'time': self.bucket.isoformat(),
            'open': float(self.open),
            'high': float(self.high),
            'low': float(self.low),
            'close': float(self.close),
            'volume': self.volume
        }


class Candle1d(Base):
    """
    1-day candles (continuous aggregate view)
    Auto-generated from intraday_candles_10m
    """
    __tablename__ = "candles_1d"
    __table_args__ = {'info': {'is_view': True}}
    
    bucket = Column(TIMESTAMP(timezone=True), primary_key=True)
    ticker_id = Column(SMALLINT, primary_key=True)
    open = Column(REAL)
    high = Column(REAL)
    low = Column(REAL)
    close = Column(REAL)
    volume = Column(Integer)
    
    def to_dict(self):
        return {
            'time': self.bucket.isoformat(),
            'open': float(self.open),
            'high': float(self.high),
            'low': float(self.low),
            'close': float(self.close),
            'volume': self.volume
        }


# ============================================================================
# Usage Examples
# ============================================================================

"""
# Query 10-min candles for RELIANCE (last 24 hours)
from datetime import datetime, timedelta

ticker_obj = db.query(Ticker).filter(Ticker.ticker == 'RELIANCE.NS').first()

candles = db.query(IntradayCandle10m).filter(
    IntradayCandle10m.ticker_id == ticker_obj.ticker_id,
    IntradayCandle10m.time >= datetime.now() - timedelta(days=1)
).order_by(IntradayCandle10m.time).all()

# Convert to JSON
candles_json = [c.to_dict() for c in candles]


# Query 1-hour candles for last week
from sqlalchemy import text

result = db.execute(text('''
    SELECT bucket, open, high, low, close, volume
    FROM candles_1h
    WHERE ticker_id = :ticker_id
    AND bucket >= NOW() - INTERVAL '7 days'
    ORDER BY bucket
'''), {'ticker_id': ticker_obj.ticker_id})

hourly_candles = [dict(row) for row in result]
"""
