"""
Check what historical data exists in database for stocks
"""
import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date, timedelta

db = SessionLocal()

# Check RELIANCE vs TCS
for ticker in ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK']:
    print(f"\n{ticker}:")
    print("=" * 40)
    
    # Get last 10 days of data
    records = db.query(models.StockData).filter(
        models.StockData.ticker == ticker
    ).order_by(models.StockData.date.desc()).limit(10).all()
    
    if records:
        for r in records:
            print(f"  {r.date}: Close ₹{r.close:.2f}")
    else:
        print("  NO DATA!")

db.close()
