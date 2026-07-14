import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from backend.database import SessionLocal, engine
from backend import models

def update_groww_logo():
    db: Session = SessionLocal()
    try:
        # User requested specific URL extraction
        # Extracted URL: https://i.pinimg.com/736x/77/ca/55/77ca550a2332ea82f01dd03bfdf6c62f.jpg
        new_logo_url = "https://i.pinimg.com/736x/77/ca/55/77ca550a2332ea82f01dd03bfdf6c62f.jpg"
        
        # Target specific tickers requested by user
        target_tickers = ["GROWW.NS", "GROWW.BO"]
        
        # Also check for base ticker just in case
        all_checks = ["GROWW", "GROWW.NS", "GROWW.BO"]
        
        print("Updating Groww logo for tickers: GROWW.NS, GROWW.BO")
        
        found_any = False
        
        # 1. Update existing specific tickers
        stocks = db.query(models.StockMetadata).filter(models.StockMetadata.ticker.in_(all_checks)).all()
        
        for stock in stocks:
            print(f"Found {stock.ticker}. Updating logo...")
            stock.logo = new_logo_url
            found_any = True
            
        # 2. If tickers don't exist, we might need to create them? 
        # The user's request implies they exist or should be there. 
        # But if they don't exist in StockMetadata, they won't show up in search unless fetched.
        # Let's assume they exist because I saw "GROWW.NS" in browser test.
        
        if not found_any:
            print("WARNING: No 'GROWW' related tickers found in database to update!")
            # Fallback search by name again
            print("Searching by name 'Groww'...")
            stocks_by_name = db.query(models.StockMetadata).filter(models.StockMetadata.name.ilike("%Groww%")).all()
            for stock in stocks_by_name:
                print(f"Found by name: {stock.ticker}. Updating logo...")
                stock.logo = new_logo_url
                found_any = True
        
        if found_any:
            db.commit()
            print("Database updated successfully.")
        else:
            print("No stocks updated.")
            
    except Exception as e:
        print(f"Error updating logo: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    update_groww_logo()
