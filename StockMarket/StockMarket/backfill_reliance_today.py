"""
Backfill missing intraday 5m candles for RELIANCE from yfinance.
Detects gaps in the IntradayCandle5Min table and fills them.
"""
import sys
sys.path.insert(0, '.')
from backend import models, database
from datetime import datetime, timedelta, date
import yfinance as yf
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

db = database.SessionLocal()

# Fetch fresh 5m data from yfinance for RELIANCE today
print("Fetching RELIANCE.NS 5m data from yfinance...")
ticker_yf = yf.Ticker("RELIANCE.NS")
df = ticker_yf.history(period="1d", interval="5m")

if df.empty:
    print("No data fetched!")
    db.close()
    sys.exit(1)

print(f"Fetched {len(df)} 5m candles from yfinance")

# Convert index to naive datetime (remove timezone)
df.index = df.index.tz_localize(None) if df.index.tz is None else df.index.tz_convert('Asia/Kolkata').tz_localize(None)

# Get existing timestamps in DB for RELIANCE today
existing = db.query(models.IntradayCandle5Min.timestamp).filter(
    models.IntradayCandle5Min.ticker == 'RELIANCE',
    models.IntradayCandle5Min.timestamp >= datetime(2026, 3, 12)
).all()
existing_ts = set(r.timestamp for r in existing)
print(f"Existing DB timestamps today: {len(existing_ts)}")

# Find gaps and upsert all yfinance candles
updated = 0
inserted = 0
skipped = 0

for ts, row in df.iterrows():
    # Snap to 5-minute boundary (remove seconds/microseconds)
    ts_naive = ts.replace(second=0, microsecond=0)
    
    o = float(row['Open'])
    h = float(row['High'])
    l = float(row['Low'])
    c = float(row['Close'])
    v = int(row['Volume'])
    
    existing_candle = db.query(models.IntradayCandle5Min).filter(
        models.IntradayCandle5Min.ticker == 'RELIANCE',
        models.IntradayCandle5Min.timestamp == ts_naive
    ).first()
    
    if existing_candle:
        # Only update if volume is 0 (our live LTP placeholder) or if yfinance has richer data
        if existing_candle.volume == 0 or existing_candle.volume is None:
            existing_candle.open = o
            existing_candle.high = h
            existing_candle.low = l
            existing_candle.close = c
            existing_candle.volume = v
            updated += 1
        else:
            # Update high/low to ensure correctness, keep volume
            existing_candle.high = max(existing_candle.high or h, h)
            existing_candle.low = min(existing_candle.low or l, l)
            existing_candle.close = c  # Always use latest close
            updated += 1
    else:
        # Insert missing candle
        new_candle = models.IntradayCandle5Min(
            ticker='RELIANCE',
            timestamp=ts_naive,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v
        )
        db.add(new_candle)
        inserted += 1

db.commit()
print(f"\nDone! Inserted: {inserted}, Updated: {updated}, Skipped: {skipped}")

# Verify final state
final_count = db.query(models.IntradayCandle5Min).filter(
    models.IntradayCandle5Min.ticker == 'RELIANCE',
    models.IntradayCandle5Min.timestamp >= datetime(2026, 3, 12)
).count()
print(f"Total RELIANCE candles for today: {final_count}")

# Show all today's candles
candles = db.query(models.IntradayCandle5Min).filter(
    models.IntradayCandle5Min.ticker == 'RELIANCE',
    models.IntradayCandle5Min.timestamp >= datetime(2026, 3, 12)
).order_by(models.IntradayCandle5Min.timestamp.asc()).all()
print("\n=== Today's RELIANCE 5m candles ===")
for c in candles:
    print(f"  {c.timestamp} | O={c.open:.2f} H={c.high:.2f} L={c.low:.2f} C={c.close:.2f} V={c.volume}")

db.close()
