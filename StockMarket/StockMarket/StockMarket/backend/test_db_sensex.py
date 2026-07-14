from database import SessionLocal
from models import StockData

db = SessionLocal()
records = db.query(StockData).filter(StockData.ticker.like('%SENSEX%')).order_by(StockData.date.desc()).limit(5).all()
for r in records:
    print(r.date, r.ticker, r.close)
