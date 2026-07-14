import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date, timedelta

db = SessionLocal()

# Check if Feb 2 is a holiday
feb2 = date(2026, 2, 2)
holiday = db.query(models.Holiday).filter(models.Holiday.date == feb2).first()

if holiday:
    print(f"Feb 2, 2026 IS a holiday: {holiday.description}")
else:
    print(f"Feb 2, 2026 is NOT in the holidays table")

# Check what data exists for Feb 2
print(f"\nChecking stock data for Feb 2...")
stock_data = db.query(models.StockData).filter(
    models.StockData.date == feb2
).limit(5).all()

if stock_data:
    print(f"Found {len(stock_data)} stocks with data for Feb 2:")
    for s in stock_data[:3]:
        print(f"  {s.ticker}: O={s.open}, H={s.high}, L={s.low}, C={s.close}")
else:
    print("No stock data found for Feb 2")

# Check recent dates
print(f"\nRecent trading days in database:")
recent = db.query(models.StockData.date).distinct().order_by(models.StockData.date.desc()).limit(10).all()
for r in recent:
    print(f"  {r[0]}")

db.close()
