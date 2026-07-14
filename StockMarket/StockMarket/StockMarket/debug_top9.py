"""
Debug script to test the top-9-history logic
"""
import yfinance as yf
from datetime import datetime, time, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.database import SessionLocal
from backend.models import StockData, Holiday

# Config
top_9 = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL']

# Index mapping
INDEX_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
}

def resolve_yf_ticker(ticker):
    if ticker in INDEX_MAP:
        return INDEX_MAP[ticker]
    return f"{ticker}.NS"

# Get holidays
db = SessionLocal()
NSE_HOLIDAYS = {h.date for h in db.query(Holiday).all()}
print(f"Loaded {len(NSE_HOLIDAYS)} holidays")

# Market status
now = datetime.now()
today = now.date()
market_open_time = time(9, 15)
market_close_time = time(15, 30)

is_trading_day = today not in NSE_HOLIDAYS and today.weekday() < 5
is_market_open = is_trading_day and market_open_time <= now.time() <= market_close_time
is_after_close = is_trading_day and now.time() > market_close_time

include_today = is_market_open or is_after_close
historical_days_needed = 4 if include_today else 5

print(f"\n=== Market Status ===")
print(f"Today: {today} (weekday={today.weekday()})")
print(f"is_trading_day: {is_trading_day}")
print(f"is_market_open: {is_market_open}")
print(f"is_after_close: {is_after_close}")
print(f"include_today: {include_today}")
print(f"historical_days_needed: {historical_days_needed}")

# Check DB for each ticker
print(f"\n=== DB Check ===")
tickers_needing_yf = []
for ticker in top_9:
    history = db.query(StockData).filter(
        StockData.ticker == ticker
    ).order_by(StockData.date.desc()).limit(historical_days_needed).all()
    print(f"{ticker}: {len(history)} records in DB")
    if len(history) < historical_days_needed:
        tickers_needing_yf.append(ticker)

print(f"\nTickers needing yfinance: {tickers_needing_yf}")

# Test yfinance fetch
print(f"\n=== YFinance Test ===")
for ticker in tickers_needing_yf[:3]:  # Test first 3
    yf_ticker = resolve_yf_ticker(ticker)
    print(f"\nFetching {ticker} ({yf_ticker})...")
    try:
        df = yf.download(yf_ticker, period="15d", interval="1d", progress=False)
        if not df.empty:
            df = df.sort_index(ascending=True)
            if include_today:
                historical_df = df[df.index.date < today].tail(historical_days_needed)
            else:
                historical_df = df.tail(historical_days_needed)
            
            print(f"  Total rows: {len(df)}, After filter: {len(historical_df)}")
            for index, row in historical_df.iterrows():
                try:
                    price = float(row['Close'].iloc[0] if hasattr(row['Close'], 'iloc') else row['Close'])
                    print(f"    {index.strftime('%b %d')}: ₹{price:.2f}")
                except Exception as e:
                    print(f"    Error parsing row: {e}")
        else:
            print(f"  Empty dataframe!")
    except Exception as e:
        print(f"  ERROR: {e}")

db.close()
print("\nDone!")
