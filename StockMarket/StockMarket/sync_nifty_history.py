"""
Sync Jan 27-30 historical data for NIFTY
"""
import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date, timedelta
import yfinance as yf

# Date range
start_date = date(2026, 1, 27)
end_date = date(2026, 1, 31)

print(f"Syncing NIFTY historical data from {start_date} to {end_date}")
print("=" * 60)

db = SessionLocal()

try:
    # Fetch from yfinance
    yf_ticker = '^NSEI'
    print(f"  Fetching NIFTY ({yf_ticker})...")
    stock = yf.Ticker(yf_ticker)
    
    hist = stock.history(start=start_date, end=end_date + timedelta(days=1))
    
    print(f"  Retrieved {len(hist)} days of data")
    
    synced_count = 0
    for idx, row in hist.iterrows():
        trade_date = idx.date()
        
        # Check if already exists
        existing = db.query(models.StockData).filter(
            models.StockData.ticker == 'NIFTY',
            models.StockData.date == trade_date
        ).first()
        
        if existing:
            continue
        
        # Insert
        new_record = models.StockData(
            ticker='NIFTY',
            date=trade_date,
            open=float(row['Open']),
            high=float(row['High']),
            low=float(row['Low']),
            close=float(row['Close']),
            volume=int(row['Volume']) if row['Volume'] else 0
        )
        
        db.add(new_record)
        synced_count += 1
        print(f"  ✓ {trade_date}: C=₹{row['Close']:.2f}")
    
    if synced_count > 0:
        db.commit()
        print(f"\n✅ Synced {synced_count} days for NIFTY")
    else:
        print(f"\n  All dates already exist")
    
except Exception as e:
    print(f"✗ Error: {e}")
    db.rollback()
finally:
    db.close()

print("=" * 60)
