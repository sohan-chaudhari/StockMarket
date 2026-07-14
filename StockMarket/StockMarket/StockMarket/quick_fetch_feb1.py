"""
Quick fetch of Feb 1st data for top 100 stocks
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from backend.database import SessionLocal
from backend.models import StockMetadata, StockData
from datetime import date
import yfinance as yf

# Top 100 most important stocks
TOP_STOCKS = [
    'NIFTY', 'BANKNIFTY', 'SENSEX',
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL', 'ITC',
    'KOTAKBANK', 'LT', 'AXISBANK', 'SBIN', 'HINDUNILVR', 'BAJFINANCE',
    'ASIANPAINT', 'MARUTI', 'TITAN', 'SUNPHARMA', 'ULTRACEMCO', 'TATAMOTORS',
    'TATASTEEL', 'WIPRO', 'NESTLEIND', 'ADANIENT', 'ADANIPORTS', 'M&M',
    'ONGC', 'NTPC', 'POWERGRID', 'JSWSTEEL', 'TECHM', 'LTIM', 'HDFCLIFE',
    'SBILIFE', 'DRREDDY', 'CIPLA', 'APOLLOHOSP', 'BRITANNIA', 'INDUSINDBK',
    'EICHERMOT', 'DIVISLAB', 'BAJAJ-AUTO', 'HEROMOTOCO', 'TATACONSUM',
    'GRASIM', 'UPL', 'ZOMATO', 'DLF', 'HAL', 'BEL', 'TRENT', 'VEDL',
    'IOC', 'GAIL', 'COALINDIA', 'BPCL', 'HINDALCO', 'ADANIGREEN',
    'ADANIPOWER', 'PIDILITIND', 'SIEMENS', 'GODREJCP', 'BOSCHLTD',
    'HAVELLS', 'DABUR', 'MARICO', 'COLPAL', 'BERGEPAINT', 'INDIGO',
    'BANKBARODA', 'PNB', 'CANBK', 'UNIONBANK', 'IDFCFIRSTB', 'FEDERALBNK',
    'BANDHANBNK', 'AUBANK', 'RBLBANK', 'YESBANK', 'IRCTC', 'IRFC',
    'RVNL', 'RECLTD', 'PFC', 'NBCC', 'BDL', 'MAZAGON', 'COCHINSHIP',
    'GRSE', 'GLENMARK', 'BIOCON', 'TORNTPHARM', 'LUPIN', 'ALKEM',
    'AUROPHARMA', 'LALPATHLAB', 'METROPOLIS', 'THYROCARE'
]

def resolve_yf_ticker(ticker):
    if ticker == 'NIFTY': return '^NSEI'
    elif ticker == 'BANKNIFTY': return '^NSEBANK'
    elif ticker == 'SENSEX': return '^BSESN'
    else: return f"{ticker}.NS"

def fetch_feb1(ticker):
    yf_ticker = resolve_yf_ticker(ticker)
    try:
        stock = yf.Ticker(yf_ticker)
        df = stock.history(start="2026-02-01", end="2026-02-02", auto_adjust=False)
        
        if not df.empty:
            row = df.iloc[0]
            return {
                "ticker": ticker,
                "date": df.index[0].date(),
                "open": float(row['Open']),
                "high": float(row['High']),
                "low": float(row['Low']),
                "close": float(row['Close']),
                "adj_close": float(row.get('Adj Close', row['Close'])),
                "volume": int(row['Volume'])
            }
    except Exception as e:
        print(f"✗ {ticker}: {e}")
    return None

print("Fetching Feb 1st data for top 100 stocks...")
print("=" * 60)

db = SessionLocal()
records = []

for i, ticker in enumerate(TOP_STOCKS, 1):
    print(f"[{i}/{len(TOP_STOCKS)}] {ticker}...", end=" ")
    result = fetch_feb1(ticker)
    if result:
        records.append(result)
        print(f"✓ Close={result['close']}")
    else:
        print("✗ No data")

print(f"\n✓ Fetched {len(records)} records")

if records:
    print("Inserting into database...")
    db.bulk_insert_mappings(StockData, records)
    db.commit()
    print(f"✓ Successfully inserted {len(records)} Feb 1st candles!")
else:
    print("✗ No data to insert")

db.close()
