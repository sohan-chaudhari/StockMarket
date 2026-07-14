import sys
sys.path.insert(0, '.')
from database import SessionLocal
from models import Candle
from sqlalchemy import text

db = SessionLocal()

# Check is_completed breakdown for SENSEX 1m
rows = db.execute(text("SELECT is_completed, COUNT(*) FROM candles WHERE ticker='SENSEX' AND timeframe='1m' GROUP BY is_completed")).fetchall()
print("SENSEX 1m is_completed breakdown:", rows)

# Check most recent timestamps
rows2 = db.execute(text("SELECT timestamp, is_completed, open, close FROM candles WHERE ticker='SENSEX' AND timeframe='1m' ORDER BY timestamp DESC LIMIT 5")).fetchall()
print("SENSEX 1m latest candles:", rows2)

# Check if is_completed column might be NULL
rows3 = db.execute(text("SELECT COUNT(*) FROM candles WHERE ticker='SENSEX' AND timeframe='1m' AND is_completed IS NULL")).fetchall()
print("NULL is_completed count:", rows3)

# Check overall is_completed stats
rows4 = db.execute(text("SELECT is_completed, COUNT(*) FROM candles GROUP BY is_completed")).fetchall()
print("Overall is_completed breakdown:", rows4)

# Check NIFTY 1m too
rows5 = db.execute(text("SELECT is_completed, COUNT(*) FROM candles WHERE ticker='NIFTY' AND timeframe='1m' GROUP BY is_completed")).fetchall()
print("NIFTY 1m is_completed breakdown:", rows5)

db.close()
