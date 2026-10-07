from pydantic import BaseModel, EmailStr, Field
from datetime import date, datetime
from typing import List, Optional, Dict

class StockDataBase(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int

    class Config:
        from_attributes = True

class StockDataResponse(StockDataBase):
    # Optional Indicator Fields
    sma_20: Optional[float] = None
    ema_20: Optional[float] = None
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_middle: Optional[float] = None

    class Config:
        extra = "allow" # Allow dynamic fields
        from_attributes = True

class IntradayCandleResponse(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    # Optional Indicator Fields
    sma_20: Optional[float] = None
    ema_20: Optional[float] = None
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_middle: Optional[float] = None

    class Config:
        extra = "allow" # Allow dynamic fields
        from_attributes = True

class IndicatorResponse(BaseModel):
    ticker: str
    indicator_type: str
    data: List[Dict] # Flexible data format based on indicator

class StockFetchRequest(BaseModel):
    ticker: str

# ==================== AUTH SCHEMAS ====================

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    user_id: int
    email: str
    full_name: Optional[str]
    virtual_balance: float
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# New Security Schemas

class VerifyEmailRequest(BaseModel):
    token: Optional[str] = None
    otp: Optional[str] = None

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class ResendVerificationRequest(BaseModel):
    email: EmailStr

class MessageResponse(BaseModel):
    message: str


# ==================== TRADING SCHEMAS ====================

class PlaceOrderRequest(BaseModel):
    ticker: str
    position_type: str  # 'LONG' or 'SHORT'
    quantity: int = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    # Optional client-generated idempotency key (one per submission attempt).
    # Replaying the same key returns the original position instead of opening
    # a second one. Omitted by older clients -> no behavior change.
    client_order_id: Optional[str] = Field(None, max_length=64)

class SetLimitsRequest(BaseModel):
    position_id: int
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None

class ClosePositionRequest(BaseModel):
    position_id: int
    closing_price: float = Field(..., gt=0)

class PositionResponse(BaseModel):
    id: int
    ticker: str
    stock_name: Optional[str] = None
    position_type: str
    quantity: int
    entry_price: float
    closing_price: Optional[float]
    total_investment: float
    realized_pnl: Optional[float]
    status: str
    close_type: Optional[str]
    created_at: datetime
    closed_at: Optional[datetime]
    # Computed fields for frontend
    unrealized_pnl: Optional[float] = None
    pnl_percentage: Optional[float] = None
    duration: Optional[str] = None

    class Config:
        from_attributes = True

class OrderResponse(BaseModel):
    id: int
    position_id: int
    ticker: str
    order_type: str
    trigger_price: float
    execution_price: Optional[float]
    status: str
    created_at: datetime
    executed_at: Optional[datetime]

    class Config:
        from_attributes = True

class TransactionResponse(BaseModel):
    id: int
    position_id: Optional[int]
    ticker: Optional[str]
    stock_name: Optional[str] = None
    transaction_type: str
    position_type: Optional[str]
    quantity: Optional[int]
    price: Optional[float]
    amount: float
    pnl: Optional[float]
    balance_after: float
    created_at: datetime

    class Config:
        from_attributes = True

class PortfolioSummary(BaseModel):
    current_balance: float
    total_invested: float
    total_realized_pnl: float
    total_unrealized_pnl: float
    total_positions: int
    open_positions: int
    closed_positions: int
    win_rate: float
    total_wins: int
    total_losses: int

class PaginatedPositions(BaseModel):
    positions: List[PositionResponse]
    total: int
    page: int
    pages: int

class PaginatedTransactions(BaseModel):
    transactions: List[TransactionResponse]
    total: int
    page: int
    pages: int

# ==================== SETTINGS SCHEMAS ====================

class ChartSettingsUpdate(BaseModel):
    ticker: Optional[str] = None
    timeframe: Optional[str] = None
    indicators: Optional[Dict] = None
    drawings: Optional[List] = None

class ChartSettingsResponse(ChartSettingsUpdate):
    user_id: int
    updated_at: datetime

    class Config:
        from_attributes = True


# ==================== WATCHLIST SCHEMAS ====================

class WatchlistItem(BaseModel):
    ticker: str
    logo: str = ""

class WatchlistResponse(BaseModel):
    watchlist: List[WatchlistItem]
    count: int
    is_default: bool = False

class WatchlistAddResponse(BaseModel):
    message: str
    ticker: str

class WatchlistRemoveResponse(BaseModel):
    message: str

# ==================== MARKET MOVERS SCHEMAS ====================

class MoverStock(BaseModel):
    ticker: str
    name: str
    current_price: float
    prev_close: float
    change: float
    change_pct: float
    volume: int
    volume_display: str
    logo: Optional[str] = None

class MarketMoversResponse(BaseModel):
    gainers: List[MoverStock]
    losers: List[MoverStock]
    most_active: List[MoverStock]
    all_stocks: List[MoverStock] = []
    market_open: bool
    last_updated: str
    advance_count: int = 0
    decline_count: int = 0

# ==================== FII/DII SCHEMAS ====================

class FiiDiiEntry(BaseModel):
    date: str
    fii_cash_cr: float
    dii_cash_cr: float
    fii_fo_cr: float
    net_total_cr: float

class FiiDiiResponse(BaseModel):
    entries: List[FiiDiiEntry]
    source: str
    last_updated: str
