from backend.database import SessionLocal
from backend.models import StockMetadata

db = SessionLocal()
stock = db.query(StockMetadata).filter_by(ticker="YESBANK").first()
if stock:
    print(f"Ticker: {stock.ticker}")
    print(f"Name: {stock.name}")
else:
    print("YESBANK not found.")
db.close()
