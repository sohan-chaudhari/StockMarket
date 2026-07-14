"""
Manual sync script to fetch Feb 2 index data (NIFTY, BANKNIFTY, SENSEX)
"""
import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date, timedelta
import yfinance as yf

# Index tickers to sync
INDEX_TICKERS = ['NIFTY', 'BANKNIFTY', 'SENSEX']

# Date to sync
target_date = date(2026, 2, 2)  # Feb 2, 2026 (Monday)

print(f"Manual sync for INDICES - {target_date.strftime('%B %d, %Y')} ({target_date.strftime('%A')})")
print("=" * 60)

db = SessionLocal()

def resolve_yf_ticker(ticker):
    """Convert ticker to yfinance format"""
    mapping = {
        'NIFTY': '^NSEI',
        'BANKNIFTY': '^NSEBANK',
        'SENSEX': '^BSESN'
    }
    return mapping.get(ticker, ticker)

synced_count = 0
failed_count = 0

for ticker in INDEX_TICKERS:
    try:
        # Check if data already exists
        existing = db.query(models.StockData).filter(
            models.StockData.ticker == ticker,
            models.StockData.date == target_date
        ).first()
        
        if existing:
            print(f"✓ {ticker:12} - Already exists (Close: ₹{existing.close:.2f})")
            continue
        
        # Fetch from yfinance
        yf_ticker = resolve_yf_ticker(ticker)
        print(f"  Fetching {ticker} ({yf_ticker})...")
        stock = yf.Ticker(yf_ticker)
        
        # Fetch data for a range around Feb 2
        start = target_date - timedelta(days=5)
        end = target_date + timedelta(days=2)
        
        hist = stock.history(start=start, end=end)
        
        print(f"  Retrieved {len(hist)} days of data")
        
        if hist.empty:
            print(f"✗ {ticker:12} - No data available from yfinance")
            failed_count += 1
            continue
        
        # Show all dates retrieved
        print(f"  Dates available:")
        for idx in hist.index:
            print(f"    - {idx.date()}")
        
        # Find Feb 2 data
        feb2_data = None
        for idx, row in hist.iterrows():
            if idx.date() == target_date:
                feb2_data = row
                break
        
        if feb2_data is None:
            print(f"✗ {ticker:12} - Feb 2 not in yfinance data (dates shown above)")
            failed_count += 1
            continue
        
        # Insert into database
        new_record = models.StockData(
            ticker=ticker,
            date=target_date,
            open=float(feb2_data['Open']),
            high=float(feb2_data['High']),
            low=float(feb2_data['Low']),
            close=float(feb2_data['Close']),
            volume=int(feb2_data['Volume']) if feb2_data['Volume'] else 0
        )
        
        db.add(new_record)
        db.commit()
        
        print(f"✓ {ticker:12} - Synced! O:{feb2_data['Open']:.2f} H:{feb2_data['High']:.2f} L:{feb2_data['Low']:.2f} C:{feb2_data['Close']:.2f}")
        synced_count += 1
        
    except Exception as e:
        print(f"✗ {ticker:12} - Error: {e}")
        import traceback
        traceback.print_exc()
        failed_count += 1
        db.rollback()

db.close()

print("=" * 60)
print(f"Sync complete: {synced_count} synced, {failed_count} failed")
