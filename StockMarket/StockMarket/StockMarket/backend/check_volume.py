from backend.database import SessionLocal
from backend import models
from sqlalchemy import desc

def check(ticker):
    db = SessionLocal()
    print(f"--- Checking {ticker} ---")
    
    # Check Newest
    newest = db.query(models.StockData).filter(models.StockData.ticker == ticker).order_by(models.StockData.date.desc()).limit(5).all()
    print("Newest:")
    for r in newest:
        print(f"{r.date}: Vol={r.volume}")
        
    # Check Oldest
    oldest = db.query(models.StockData).filter(models.StockData.ticker == ticker).order_by(models.StockData.date.asc()).limit(5).all()
    print("Oldest:")
    for r in oldest:
        print(f"{r.date}: Vol={r.volume}")
        
    db.close()

if __name__ == "__main__":
    check("BANKNIFTY") # Try user ticker
    check("^NSEBANK")  # Try Yahoo ticker
    check("SENSEX")
    check("^BSESN")
