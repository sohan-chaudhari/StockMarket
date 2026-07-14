from database import SessionLocal
from models import IntradayCandle5Min
from sqlalchemy import update, text
db = SessionLocal()
# Indices fetched by yfinance that might be shifted
indices = ['NIFTY', 'BANKNIFTY', 'SENSEX']
# Update timestamps by subtracting 5 hours and 30 minutes
# only for candles that are shifted (i.e. timestamp time is out of normal market hours like > 15:30)
db.execute(text("UPDATE intraday_candles_5min SET timestamp = timestamp - interval '5 hours 30 minutes' WHERE ticker IN ('NIFTY', 'BANKNIFTY', 'SENSEX') AND EXTRACT(HOUR FROM timestamp) >= 16;"))
db.commit()
# For June 11, 12, 17, 18 where it started around 13:00 or 14:00, we can just subtract 5.5 hours where hour >= 13 and date > '2026-06-08'
db.execute(text("UPDATE intraday_candles_5min SET timestamp = timestamp - interval '5 hours 30 minutes' WHERE ticker IN ('NIFTY', 'BANKNIFTY', 'SENSEX') AND timestamp >= '2026-06-09' AND EXTRACT(HOUR FROM timestamp) >= 13;"))
db.commit()
print('Done')
