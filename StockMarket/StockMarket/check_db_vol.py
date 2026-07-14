
from backend.database import SessionLocal
from backend.models import StockData, CurrentDayCandle
import pandas as pd

db = SessionLocal()
ticker = "^NSEBANK"
print(f"--- Checking History for {ticker} ---")
data = db.query(StockData).filter(StockData.ticker == ticker).order_by(StockData.date.desc()).limit(10).all()

for d in data:
    print(f"Date: {d.date} | Close: {d.close} | Volume: {d.volume}")

print(f"\n--- Checking Live Candle for {ticker} ---")
candle = db.query(CurrentDayCandle).filter(CurrentDayCandle.ticker == ticker).first()
if candle:
    print(f"Date: {candle.trading_date} | Finalized: {candle.is_finalized} | Volume: {candle.volume}")
else:
    print("No live candle found.")

db.close()
