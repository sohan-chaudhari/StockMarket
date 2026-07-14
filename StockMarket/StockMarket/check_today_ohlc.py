import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date

db = SessionLocal()

today = date.today()
print(f"Checking OHLC data for {today}...")

# Get today's candles
candles = db.query(models.CurrentDayCandle).filter(
    models.CurrentDayCandle.trading_date == today
).limit(10).all()

if candles:
    print(f"\nFound {len(candles)} candles for today:")
    for candle in candles:
        print(f"\n{candle.ticker}:")
        print(f"  Open: {candle.open}")
        print(f"  High: {candle.high}")
        print(f"  Low: {candle.low}")
        print(f"  Current: {candle.current_price}")
        print(f"  Close: {candle.close}")
        print(f"  Volume: {candle.volume}")
        print(f"  Finalized: {candle.is_finalized}")
        print(f"  Last Updated: {candle.last_updated}")
else:
    print("No candles found for today!")

db.close()
