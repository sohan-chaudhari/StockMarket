import sys; sys.path.insert(0, '.')
from database import SessionLocal
from models import Candle
from datetime import timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
from sqlalchemy import func

db = SessionLocal()
# Get first and last candle of each day for SENSEX 1m
from sqlalchemy import cast, Date
rows = db.query(
    cast(Candle.timestamp, Date).label('day'),
    func.min(Candle.timestamp).label('first_ts'),
    func.max(Candle.timestamp).label('last_ts')
).filter(
    Candle.ticker == 'SENSEX',
    Candle.timeframe == '1m'
).group_by(cast(Candle.timestamp, Date)).order_by(cast(Candle.timestamp, Date).desc()).limit(7).all()

from datetime import datetime

for r in rows:
    first = r.first_ts
    last = r.last_ts
    # Convert to proper epoch as the backend does
    first_epoch = int(first.replace(tzinfo=IST).timestamp())
    # What the chart shows
    import datetime as dt
    utc_first = dt.datetime.fromtimestamp(first_epoch, tz=timezone.utc)
    print(f"Day: {r.day}  First: {first} (epoch={first_epoch})  UTC back: {utc_first.strftime('%H:%M')} UTC = {utc_first.hour*60+utc_first.minute+330} min IST")

db.close()
