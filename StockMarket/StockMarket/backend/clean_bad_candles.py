from backend.database import SessionLocal
from backend import models
from datetime import date

def clean():
    db = SessionLocal()
    today = date(2025, 12, 25)
    
    print(f"Cleaning candles for {today}...")
    
    deleted = db.query(models.CurrentDayCandle).filter(
        models.CurrentDayCandle.trading_date == today
    ).delete()
    
    db.commit()
    print(f"Deleted {deleted} bad candles.")
    db.close()

if __name__ == "__main__":
    clean()
