import sys
import os

# Add parent directory to path to import backend modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import SessionLocal
from backend.models import StockMetadata

def clean_duplicates():
    db = SessionLocal()
    try:
        print("Checking for duplicate 'Groww Stock' entries...")
        
        # Find entries with name "Groww Stock"
        duplicates = db.query(StockMetadata).filter(StockMetadata.name == "Groww Stock").all()
        
        if duplicates:
            print(f"Found {len(duplicates)} entry/entries for 'Groww Stock':")
            for stock in duplicates:
                print(f" - ID: {stock.id}, Ticker: {stock.ticker}, Name: {stock.name}, Exchange: {stock.exchange}")
            
            # Delete them
            for stock in duplicates:
                db.delete(stock)
            
            db.commit()
            print(f"Successfully deleted {len(duplicates)} row(s).")
        else:
            print("No entries found for 'Groww Stock'.")
            
    except Exception as e:
        print(f"An error occurred: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    clean_duplicates()
