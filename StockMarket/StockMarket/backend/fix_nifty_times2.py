from database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
db.execute(text("DELETE FROM intraday_candles_5min WHERE ticker IN ('NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY') AND EXTRACT(HOUR FROM timestamp) >= 16;"))
db.commit()
print('Done')
