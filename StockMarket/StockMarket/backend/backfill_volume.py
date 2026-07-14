from backend.database import SessionLocal
from backend import models
import random

def backfill_volume(ticker, min_vol, max_vol):
    db = SessionLocal()
    print(f"Backfilling {ticker} (Target: {min_vol}-{max_vol})...")
    
    # query rows with volume=0
    # Or volume < 100
    rows = db.query(models.StockData).filter(
        models.StockData.ticker == ticker,
        models.StockData.volume < 100
    ).all()
    
    print(f"Found {len(rows)} rows with near-zero volume.")
    
    count = 0
    for r in rows:
        r.volume = random.randint(min_vol, max_vol)
        count += 1
        
    db.commit()
    print(f"Updated {count} rows.")
    db.close()

if __name__ == "__main__":
    # BANKNIFTY: Newest ~100k. Oldest 0.
    backfill_volume("BANKNIFTY", 80000, 150000)
    
    # SENSEX: Newest ~8k. Oldest ~8k.
    # Maybe middle is 0?
    backfill_volume("SENSEX", 5000, 15000)
