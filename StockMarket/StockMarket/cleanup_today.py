from backend import database, models
from datetime import date

def cleanup():
    db = database.SessionLocal()
    today = date.today()
    print(f"Checking for data on {today}...")

    # 1. Check StockData (History)
    # This should NOT exist for today theoretically
    stock_rows = db.query(models.StockData).filter(models.StockData.date == today).all()
    print(f"Found {len(stock_rows)} rows in StockData for {today}.")
    
    # 2. Check CurrentDayCandle (Live)
    # This might exist if process_ticker ran pre-market
    candle_rows = db.query(models.CurrentDayCandle).filter(models.CurrentDayCandle.trading_date == today).all()
    print(f"Found {len(candle_rows)} rows in CurrentDayCandle for {today}.")

    if stock_rows:
        print("Deleting premature StockData...")
        count = db.query(models.StockData).filter(models.StockData.date == today).delete()
        print(f"Deleted {count} rows.")

    if candle_rows:
        print("Deleting premature CurrentDayCandle...")
        count = db.query(models.CurrentDayCandle).filter(models.CurrentDayCandle.trading_date == today).delete()
        print(f"Deleted {count} rows.")

    db.commit()
    db.close()

if __name__ == "__main__":
    cleanup()
