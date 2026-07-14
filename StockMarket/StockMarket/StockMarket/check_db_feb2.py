import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date

db = SessionLocal()

feb2 = date(2026, 2, 2)

# Check NIFTY data
print("Checking NIFTY data for Feb 2...")
nifty_data = db.query(models.StockData).filter(
    models.StockData.ticker == 'NIFTY',
    models.StockData.date == feb2
).first()

if nifty_data:
    print(f"✓ NIFTY Feb 2: O={nifty_data.open}, H={nifty_data.high}, L={nifty_data.low}, C={nifty_data.close}")
else:
    print("✗ No NIFTY data for Feb 2")

# Check RELIANCE data
print("\nChecking RELIANCE data for Feb 2...")
reliance_data = db.query(models.StockData).filter(
    models.StockData.ticker == 'RELIANCE',
    models.StockData.date == feb2
).first()

if reliance_data:
    print(f"✓ RELIANCE Feb 2: O={reliance_data.open}, H={reliance_data.high}, L={reliance_data.low}, C={reliance_data.close}")
else:
    print("✗ No RELIANCE data for Feb 2")

# Check what dates we have around Feb 2
print("\nDates available for NIFTY:")
dates = db.query(models.StockData.date).filter(
    models.StockData.ticker == 'NIFTY'
).order_by(models.StockData.date.desc()).limit(10).all()

for d in dates:
    print(f"  {d[0]}")

db.close()
