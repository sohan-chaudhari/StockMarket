import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models

db = SessionLocal()

for ticker in ['BANKNIFTY', 'SENSEX']:
    print(f"\n{ticker} in database:")
    records = db.query(models.StockData).filter(
        models.StockData.ticker == ticker
    ).order_by(models.StockData.date.desc()).limit(10).all()
    
    if records:
        for r in records:
            print(f"  {r.date}: Close ₹{r.close:.2f}")
    else:
        print("  NO DATA!")

db.close()
