from backend.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
print('5m:', db.execute(text("SELECT count(1) FROM intraday_candles_5min WHERE ticker='MEESHO'")).scalar())
print('15m:', db.execute(text("SELECT count(1) FROM intraday_candles_15min WHERE ticker='MEESHO'")).scalar())
print('1d:', db.execute(text("SELECT count(1) FROM stock_data WHERE ticker='MEESHO'")).scalar())
