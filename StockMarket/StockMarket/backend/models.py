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
        # DB-03: same redundancy as Candle above -- UniqueConstraint already
        # backs these exact columns/order with its own index. This model-only
        # duplicate never actually got created on the live DB (SQLAlchemy
        # only auto-creates indexes for brand-new tables, and stock_data
        # already existed), but leaving it here would create it on any fresh
        # database. ix_stock_data_ticker_date_desc (startup-injected, DESC
        # order) is the genuinely useful second index.
        UniqueConstraint('ticker', 'date', name='uix_ticker_date'),
    )

class IntradayTick(Base):
    # DB-09: dead in the live app (superseded by the unified `candles` table
    # + aggregator.py), renamed live to make that unambiguous rather than
    # dropped -- 8,753 rows of real historical data, kept for now.
    __tablename__ = "deprecated_intraday_ticks"

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
    # DB-09: dead in the live app (superseded by the unified `candles`
    # table), renamed live rather than dropped -- 7.2M rows of real
    # historical data, kept for now.
    __tablename__ = "deprecated_intraday_candles_5min"

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
    # DB-09: same rationale as IntradayCandle5Min above.
    __tablename__ = "deprecated_intraday_candles_15min"

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
    # DB-09: same rationale as IntradayCandle5Min above.
    __tablename__ = "deprecated_intraday_candles_1min"

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
        # DB-03: no separate Index() here -- UniqueConstraint already creates
        # a backing btree index on these exact columns/order. A duplicate
        # Index("idx_candle_lookup", ...) used to sit here too, tripling
        # write cost on this table's every insert with zero query benefit
        # (ix_candle_ticker_tf_ts below, DESC-ordered, is the genuinely
        # useful second index for this app's "latest first" queries).
        UniqueConstraint("ticker", "timeframe", "timestamp", name="uix_candle_key"),
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


class FiiDiiFlow(Base):
    """One row per trading session of FII/DII cash + F&O flows.

    /api/fii-dii scrapes NSE and Moneycontrol, both of which fail intermittently.
    Persisting each successful scrape means a failed one can fall back to the last
    known-good figures instead of showing N/A, and history accumulates past the
    5 sessions the upstream page exposes.
    """
    __tablename__ = "fii_dii_flows"

    date = Column(String(20), primary_key=True, index=True)
    fii_cash_cr = Column(Float, default=0.0)
    dii_cash_cr = Column(Float, default=0.0)
    fii_fo_cr = Column(Float, default=0.0)
    net_total_cr = Column(Float, default=0.0)
    source = Column(String(40))
    updated_at = Column(DateTime, default=get_ist_now, onupdate=get_ist_now)


class RetentionJob(Base):
    __tablename__ = "retention_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="RUNNING")
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    last_ticker = Column(String(50), nullable=True)
    rows_source = Column(BigInteger, default=0)
    rows_target = Column(BigInteger, default=0)
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

class IntradayPrefillJob(Base):
    """One row per NSE trading session. Tracks the daily 5m prefill run."""
    __tablename__ = "intraday_prefill_jobs"

    id               = Column(Integer, primary_key=True, index=True)
    session_date     = Column(Date, nullable=False, unique=True)
    status           = Column(String(20), nullable=False, default="RUNNING")
    tickers_total    = Column(Integer, default=0)
    tickers_skipped  = Column(Integer, default=0)
    tickers_fetched  = Column(Integer, default=0)
    tickers_no_data  = Column(Integer, default=0)
    tickers_partial  = Column(Integer, default=0)
    tickers_failed   = Column(Integer, default=0)
    candles_inserted = Column(Integer, default=0)
    candles_existed  = Column(Integer, default=0)
    started_at       = Column(DateTime, server_default=func.now())
    completed_at     = Column(DateTime, nullable=True)
    error_detail     = Column(String(2000), nullable=True)


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
    exit_reason = Column(String(20), nullable=True)  # 'TP_HIT', 'SL_HIT', 'MANUAL_CLOSE'
    take_profit = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    tp_edit_count = Column(Integer, default=0)  # Max 3 edits allowed
    sl_edit_count = Column(Integer, default=0)  # Max 3 edits allowed
    created_at = Column(DateTime, default=get_ist_now, index=True)
    closed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # DB-07: the open/closed-positions endpoints always filter by both
        # columns together ("this user's OPEN positions" / "...CLOSED...").
        # Separate single-column indexes on user_id and status already
        # existed; this composite serves that exact query pattern directly
        # instead of relying on a bitmap AND of the two.
        Index("idx_positions_user_status", "user_id", "status"),
    )


class Order(Base):
    """Tracks Take Profit, Stop Loss, and Queued AMO entry orders"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, ForeignKey("positions.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    ticker = Column(String(50), nullable=False, index=True)
    stock_name = Column(String(255), nullable=True)
    order_type = Column(String(20), nullable=False)  # 'TP', 'SL', 'AMO_ENTRY'
    position_type = Column(String(10), nullable=True)  # 'LONG' or 'SHORT' (for AMO_ENTRY)
    quantity = Column(Integer, nullable=True)  # (for AMO_ENTRY)
    trigger_price = Column(Float, nullable=False)  # (for TP/SL: trigger price; for AMO: requested entry price)
    execution_price = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)  # (for AMO_ENTRY)
    stop_loss = Column(Float, nullable=True)  # (for AMO_ENTRY)
    locked_amount = Column(Float, nullable=True)  # (for AMO_ENTRY)
    status = Column(String(20), default='PENDING', index=True)  # 'PENDING', 'EXECUTED', 'CANCELLED', 'QUEUED_AMO'
    created_at = Column(DateTime, default=get_ist_now)
    executed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # DB-07: same rationale as Position -- "this user's PENDING orders"
        # is the common access pattern.
        Index("idx_orders_user_status", "user_id", "status"),
    )


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


class IntradayCandle1H(Base):
    # DB-09: same rationale as IntradayCandle5Min above.
    __tablename__ = "deprecated_intraday_candles_1h"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uix_ticker_timestamp_1h'),
        Index('idx_1h_ticker_ts', 'ticker', 'timestamp'),
    )

class IntradayCandle30Min(Base):
    # DB-09: same rationale as IntradayCandle5Min above.
    __tablename__ = "deprecated_intraday_candles_30min"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uix_ticker_timestamp_30min'),
        Index('idx_30min_ticker_ts', 'ticker', 'timestamp'),
    )
