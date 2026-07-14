from backend.database import SessionLocal
from backend.models import IntradayCandle5Min
db = SessionLocal()
count = db.query(IntradayCandle5Min).filter(IntradayCandle5Min.ticker == 'RELIANCE').count()
print(f'RELIANCE candles: {count}')
