"""Quick simulation of the fixed endpoint logic to verify candles will be returned."""
import sys
sys.path.insert(0, '.')
from database import SessionLocal, get_ist_now
from models import Candle
from datetime import timedelta

db = SessionLocal()
ist_now = get_ist_now()

# Simulate OLD cutoff
old_cutoff = ist_now - timedelta(days=1)
# Simulate NEW cutoff
new_cutoff = ist_now - timedelta(days=7)

print(f"Current IST: {ist_now}")
print(f"OLD 1m cutoff (1 day):  {old_cutoff}")
print(f"NEW 1m cutoff (7 days): {new_cutoff}")
print()

for ticker in ["SENSEX", "NIFTY", "BANKNIFTY", "HDFCBANK"]:
    for interval in ["1m", "5m", "15m"]:
        old_q = db.query(Candle).filter(
            Candle.ticker == ticker, Candle.timeframe == interval,
            Candle.timestamp >= (ist_now - timedelta(days={"1m":1,"5m":7,"15m":30}.get(interval,7))).replace(tzinfo=None),
            Candle.is_completed == True,
        ).count()
        new_q = db.query(Candle).filter(
            Candle.ticker == ticker, Candle.timeframe == interval,
            Candle.timestamp >= (ist_now - timedelta(days={"1m":7,"5m":10,"15m":45}.get(interval,10))).replace(tzinfo=None),
            Candle.is_completed == True,
        ).count()
        status = "FIXED" if old_q == 0 and new_q > 0 else ("OK" if old_q > 0 else "NO DATA")
        print(f"  {ticker:12} {interval:4}  OLD={old_q:4}  NEW={new_q:4}  [{status}]")

db.close()
