"""Check if Feb 1st data exists in database"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from backend.database import SessionLocal
from backend.models import StockData
from datetime import date

db = SessionLocal()

# Check Feb 1st
feb_1 = date(2026, 2, 1)
count = db.query(StockData).filter(StockData.date == feb_1).count()

print(f"Feb 1st records in database: {count}")

if count > 0:
    samples = db.query(StockData).filter(StockData.date == feb_1).limit(10).all()
    print("\nSample Feb 1st records:")
    for s in samples:
        print(f"  {s.ticker}: Open={s.open}, Close={s.close}")
else:
    print("\n✗ NO Feb 1st data in database!")
    print("  The fetch_feb1_candles.py script may still be running...")

# Check RELIANCE specifically
reliance_feb = db.query(StockData).filter(
    StockData.ticker == 'RELIANCE',
    StockData.date >= date(2026, 1, 28),
    StockData.date <= date(2026, 2, 2)
).order_by(StockData.date).all()

print(f"\nRELIANCE data (Jan 28 - Feb 2):")
for r in reliance_feb:
    print(f"  {r.date}: Close={r.close}")

db.close()
