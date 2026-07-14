"""
Script to clean up stock data for holiday dates.
"""
from datetime import date
from backend.database import SessionLocal, engine
from backend import models

def cleanup_holiday_data(holiday_date: date):
    db = SessionLocal()
    try:
        # 1. Delete from stock_data table
        deleted_stock = db.query(models.StockData).filter(
            models.StockData.date == holiday_date
        ).delete()
        
        # 2. Delete from current_day_candle table
        deleted_candle = db.query(models.CurrentDayCandle).filter(
            models.CurrentDayCandle.trading_date == holiday_date
        ).delete()
        
        db.commit()
        print(f"Cleaned up data for {holiday_date}:")
        print(f"  - Deleted {deleted_stock} records from stock_data")
        print(f"  - Deleted {deleted_candle} records from current_day_candle")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    # Clean up January 15, 2026 data
    cleanup_holiday_data(date(2026, 1, 15))
