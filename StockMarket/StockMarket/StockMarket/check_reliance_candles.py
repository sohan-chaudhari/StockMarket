import sys
sys.path.insert(0, '.')
from backend import models, database
from datetime import date, datetime, timedelta

db = database.SessionLocal()

# Check RELIANCE candles - most recent first
candles = db.query(models.IntradayCandle5Min).filter(
    models.IntradayCandle5Min.ticker == 'RELIANCE'
).order_by(models.IntradayCandle5Min.timestamp.desc()).limit(20).all()

print('=== Last 20 RELIANCE 5m candles ===')
for c in candles:
    status = 'OK'
    if c.open is None or c.high is None or c.low is None or c.close is None:
        status = 'INCOMPLETE'
    elif c.close == c.open and c.high == c.open and c.low == c.open:
        status = 'FLAT'
    print(f'{c.timestamp} | O={c.open} H={c.high} L={c.low} C={c.close} V={c.volume} | {status}')

# Date range
oldest = db.query(models.IntradayCandle5Min).filter(
    models.IntradayCandle5Min.ticker == 'RELIANCE'
).order_by(models.IntradayCandle5Min.timestamp.asc()).first()
newest = db.query(models.IntradayCandle5Min).filter(
    models.IntradayCandle5Min.ticker == 'RELIANCE'
).order_by(models.IntradayCandle5Min.timestamp.desc()).first()

if oldest and newest:
    print(f'Date range: {oldest.timestamp} to {newest.timestamp}')

# Today count
today_count = db.query(models.IntradayCandle5Min).filter(
    models.IntradayCandle5Min.ticker == 'RELIANCE',
    models.IntradayCandle5Min.timestamp >= datetime(2026, 3, 12)
).count()
print(f"Today 5m candle count: {today_count}")
db.close()
