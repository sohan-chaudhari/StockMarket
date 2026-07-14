from backend.database import SessionLocal
from backend import models

def count_stocks():
    db = SessionLocal()
    count = db.query(models.StockMetadata).count()
    print(f"Total Stocks in Database: {count}")
    
    # Show first 5
    stocks = db.query(models.StockMetadata).limit(5).all()
    print("\n--- Samples ---")
    for s in stocks:
        print(f"Ticker: {s.ticker}, Name: {s.name}")
        
    db.close()

if __name__ == "__main__":
    count_stocks()
