from sqlalchemy import Column, Date, Float, Integer, String, UniqueConstraint, DateTime, Boolean, TIMESTAMP, JSON, BigInteger, ForeignKey, Index
from sqlalchemy.sql import func
from datetime import datetime
from database import Base, get_ist_now

class StockData(Base):
    __tablename__ = "stock_data"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    date = Column(Date, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    adj_close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'date', name='uix_ticker_date'),
        Index('idx_stock_data_ticker_date', 'ticker', 'date'),
    )

class IntradayTick(Base):
    __tablename__ = "intraday_ticks"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    price = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    trading_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())

class CurrentDayCandle(Base):
    __tablename__ = "current_day_candle"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    trading_date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    close = Column(Float, nullable=True) # Null until finalized
    volume = Column(Integer, default=0)
    is_finalized = Column(Boolean, default=False)
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('ticker', 'trading_date', name='uix_ticker_trading_date'),
    )

class IntradayCandle5Min(Base):
    __tablename__ = "intraday_candles_5min"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uix_ticker_timestamp_5min'),
        Index('idx_5min_ticker_ts', 'ticker', 'timestamp'),
    )

class IntradayCandle15Min(Base):
    __tablename__ = "intraday_candles_15min"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uix_ticker_timestamp_15min'),
        Index('idx_15min_ticker_ts', 'ticker', 'timestamp'),
    )

class IntradayCandle1Min(Base):
    __tablename__ = "intraday_candles_1min"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uix_ticker_timestamp_1min'),
        Index('idx_1min_ticker_ts', 'ticker', 'timestamp'),
    )


# ── Unified Candle Table ──────────────────────────────────────────────
# Single table for ALL timeframes: 1m,5m,15m,30m,1h,1D,1W,1M
TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "1D", "1W", "1M"]

class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, nullable=False, index=True)
    timeframe = Column(String(5), nullable=False)    # "1m","5m","15m","30m","1h","1D","1W","1M"
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, default=0)
    is_completed = Column(Boolean, default=True)      # False for the in-progress "forming" candle
    data_source = Column(String(10), default="ANGELONE")  # ANGELONE, YFINANCE, BACKFILL
    is_backfilled = Column(Boolean, default=False)    # True if loaded from historical backfill
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("ticker", "timeframe", "timestamp", name="uix_candle_key"),
        Index("idx_candle_lookup", "ticker", "timeframe", "timestamp"),
    )


class MarketSession(Base):
    """Defines trading sessions (normal, muhurat, special) with start/end times."""
    __tablename__ = "market_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_date = Column(Date, nullable=False, index=True)
    session_type = Column(String(20), default="NORMAL")  # NORMAL, MUHURAT, SPECIAL
    open_time = Column(DateTime, nullable=False)         # IST
    close_time = Column(DateTime, nullable=False)        # IST
    description = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("session_date", "session_type", name="uix_session"),
    )

class Holiday(Base):
    __tablename__ = "holidays"
    
    date = Column(Date, primary_key=True, index=True)
    description = Column(String)
    created_at = Column(DateTime, default=get_ist_now)

class StockMetadata(Base):
    __tablename__ = "stock_metadata"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    name = Column(String)
    exchange = Column(String)
    logo = Column(String, nullable=True)
    base_price = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    is_premium = Column(Boolean, default=False)
    sector = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint('ticker', 'exchange', name='uix_ticker_exchange'),
    )

# ==================== AUTH MODELS ====================

class User(Base):
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    virtual_balance = Column(Float, default=100000.00)  # Starting paper trading balance
    is_verified = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=get_ist_now)
    last_login = Column(DateTime, nullable=True)
    # Security fields
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)

class UserChartSettings(Base):
    __tablename__ = "user_chart_settings"
    __table_args__ = (
        UniqueConstraint('user_id', 'ticker', name='uix_user_chart_settings'),
    )
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    ticker = Column(String)
    timeframe = Column(String, default="5m")
    indicators = Column(JSON, default=dict)
    drawings = Column(JSON, default=list)
    updated_at = Column(DateTime, default=get_ist_now, onupdate=get_ist_now)


class LoginAttempt(Base):
    """Audit log for login attempts"""
    __tablename__ = "login_attempts"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), index=True)
    ip_address = Column(String(45))  # IPv6 compatible
    user_agent = Column(String(500), nullable=True)
    success = Column(Boolean, default=False)
    failure_reason = Column(String(100), nullable=True)
    attempted_at = Column(DateTime, default=get_ist_now, index=True)


class VerificationToken(Base):
    """Tokens for email verification and password reset"""
    __tablename__ = "verification_tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    token_hash = Column(String(255), nullable=False)
    otp_code = Column(String(6), nullable=True)  # 6-digit OTP
    token_type = Column(String(50), nullable=False)  # 'email_verification' or 'password_reset'
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=get_ist_now)


class TokenBlacklist(Base):
    """Blacklisted JWT tokens (for logout)"""
    __tablename__ = "token_blacklist"
    
    id = Column(Integer, primary_key=True, index=True)
    token_jti = Column(String(255), unique=True, nullable=False, index=True)
    blacklisted_at = Column(DateTime, default=get_ist_now)


# ==================== TRADING MODELS ====================

class Position(Base):
    """Tracks open and closed trading positions"""
    __tablename__ = "positions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    ticker = Column(String(50), nullable=False, index=True)
    stock_name = Column(String(255), nullable=True)  # Snapshot of company name
    position_type = Column(String(10), nullable=False)  # 'LONG' or 'SHORT'
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    closing_price = Column(Float, nullable=True)
    total_investment = Column(Float, nullable=False)  # entry_price * quantity
    realized_pnl = Column(Float, nullable=True)
    status = Column(String(20), default='OPEN', index=True)  # 'OPEN', 'CLOSED'
    close_type = Column(String(20), nullable=True)  # 'MANUAL', 'TP_EXECUTED', 'SL_EXECUTED'
    take_profit = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    tp_edit_count = Column(Integer, default=0)  # Max 3 edits allowed
    sl_edit_count = Column(Integer, default=0)  # Max 3 edits allowed
    created_at = Column(DateTime, default=get_ist_now, index=True)
    closed_at = Column(DateTime, nullable=True)


class Order(Base):
    """Tracks Take Profit and Stop Loss orders"""
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    ticker = Column(String(50), nullable=False, index=True)
    order_type = Column(String(10), nullable=False)  # 'TP' or 'SL'
    trigger_price = Column(Float, nullable=False)
    execution_price = Column(Float, nullable=True)
    status = Column(String(20), default='PENDING', index=True)  # 'PENDING', 'EXECUTED', 'CANCELLED'
    created_at = Column(DateTime, default=get_ist_now)
    executed_at = Column(DateTime, nullable=True)


class Transaction(Base):
    """Immutable ledger of all balance movements"""
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    position_id = Column(Integer, ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True)
    ticker = Column(String(50), nullable=True)
    stock_name = Column(String(255), nullable=True)
    transaction_type = Column(String(30), nullable=False)  # 'OPEN', 'CLOSE', 'TP_EXECUTED', 'SL_EXECUTED'
    position_type = Column(String(10), nullable=True)  # 'LONG' or 'SHORT'
    quantity = Column(Integer, nullable=True)
    price = Column(Float, nullable=True)
    amount = Column(Float, nullable=False)  # Positive for credits, negative for debits
    pnl = Column(Float, nullable=True)  # Profit/Loss for this transaction
    balance_after = Column(Float, nullable=False)
    created_at = Column(DateTime, default=get_ist_now, index=True)


# ==================== WATCHLIST MODEL ====================

class UserWatchlist(Base):
    """User's personal stock watchlist stored in PostgreSQL"""
    __tablename__ = "user_watchlist"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    ticker = Column(String(50), nullable=False)
    added_at = Column(DateTime, default=get_ist_now)

    __table_args__ = (
        UniqueConstraint('user_id', 'ticker', name='uix_user_watchlist_ticker'),
    )
