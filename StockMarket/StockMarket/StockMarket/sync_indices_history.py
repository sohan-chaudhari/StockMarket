"""
Sync Jan 27-30 historical data for BANKNIFTY and SENSEX
"""
import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date, timedelta
import yfinance as yf

# Indices to sync
INDICES = ['BANKNIFTY', 'SENSEX']

# Date range
start_date = date(2026, 1, 27)
end_date = date(2026, 1, 31)

print(f"Syncing index historical data from {start_date} to {end_date}")
print("=" * 60)

db = SessionLocal()

def resolve_yf_ticker(ticker):
    mapping = {
        'BANKNIFTY': '^NSEBANK',
        'SENSEX': '^BSESN'
    }
    return mapping.get(ticker, ticker)

total_synced = 0

for ticker in INDICES:
    print(f"\n{ticker}:")
    try:
        yf_ticker = resolve_yf_ticker(ticker)
        stock = yf.Ticker(yf_ticker)
        
        hist = stock.history(start=start_date, end=end_date + timedelta(days=1))
        
        if hist.empty:
            print(f"  ✗ No data from yfinance")
            continue
        
        synced_count = 0
        for idx, row in hist.iterrows():
            trade_date = idx.date()
            
            # Check if already exists
            existing = db.query(models.StockData).filter(
                models.StockData.ticker == ticker,
                models.StockData.date == trade_date
            ).first()
            
            if existing:
                continue
            
            # Insert
            new_record = models.StockData(
                ticker=ticker,
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
            total_synced += synced_count
            print(f"  Synced {synced_count} days")
        else:
            print(f"  All dates already exist")
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        db.rollback()

db.close()

print("\n" + "=" * 60)
print(f"Total synced: {total_synced} records")
