"""Test the 30m intraday endpoint to reproduce the 500 error."""
import sys
sys.path.insert(0, '.')
from database import SessionLocal, get_ist_now
from models import Candle, TIMEFRAMES
from datetime import timedelta
from sqlalchemy import text

db = SessionLocal()

# Check what 30m data exists
rows = db.execute(text("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM candles WHERE timeframe='30m'")).fetchall()
print("30m candles overall:", rows)

rows2 = db.execute(text("SELECT ticker, COUNT(*) FROM candles WHERE timeframe='30m' GROUP BY ticker ORDER BY COUNT(*) DESC LIMIT 10")).fetchall()
print("30m by ticker:", rows2)

# Now simulate the endpoint
from datetime import timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))

interval = "30m"
ist_now = get_ist_now()
days_back = {"1m": 7, "5m": 10, "15m": 45, "30m": 90, "1h": 120}.get(interval, 10)
cutoff_dt = ist_now - timedelta(days=days_back)
print(f"\nCutoff for 30m: {cutoff_dt}")

# Check SENSEX 30m
candles = db.query(Candle).filter(
    Candle.ticker == 'SENSEX',
    Candle.timeframe == interval,
    Candle.timestamp >= cutoff_dt.replace(tzinfo=None),
    Candle.is_completed == True,
).order_by(Candle.timestamp.asc()).limit(10).all()
print(f"SENSEX 30m in window: {len(candles)}")

# Check aggregator import
try:
    from aggregator import (
        snap_to_nse_session, is_trading_day, is_market_hour,
        ist_now_naive, fix_ohlc, validate_ohlc, candle_aggregator, TIMEFRAME_BUCKETS
    )
    print("Aggregator import OK")
    live = candle_aggregator.get_current('SENSEX')
    print(f"Live data for SENSEX: {live}")
    if live and interval in live:
        print(f"Live 30m candle: {live[interval]}")
    else:
        print(f"No live 30m candle (keys: {list(live.keys()) if live else 'None'})")
except Exception as e:
    print(f"Aggregator ERROR: {e}")
    import traceback; traceback.print_exc()

db.close()
