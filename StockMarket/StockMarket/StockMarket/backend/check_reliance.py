from backend.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
res = db.execute(text("SELECT timestamp, open, high, low, close FROM intraday_candles_15min WHERE ticker='RELIANCE' AND timestamp >= '2026-03-01' ORDER BY timestamp ASC")).fetchall()
print(f"Count: {len(res)}")
if res:
    print(f"First 3: {res[:3]}")
    print(f"Last 3: {res[-3:]}")
res_all = db.execute(text("SELECT count(*) FROM intraday_candles_15min WHERE ticker='RELIANCE'")).scalar()
print(f"Total Count for RELIANCE: {res_all}")
res_infy = db.execute(text("SELECT count(*) FROM intraday_candles_15min WHERE ticker='INFY'")).scalar()
res_max = db.execute(text("SELECT max(timestamp) FROM intraday_candles_15min WHERE ticker='RELIANCE'")).scalar()
print(f"Max date for RELIANCE: {res_max}")
res_min = db.execute(text("SELECT min(timestamp) FROM intraday_candles_15min WHERE ticker='RELIANCE'")).scalar()
print(f"Min date for RELIANCE: {res_min}")

