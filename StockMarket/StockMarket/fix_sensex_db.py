from backend import models, database

target_ticker = "SENSEX.NS"

print(f"--- Cleaning DB for {target_ticker} ---")
db = database.SessionLocal()

try:
    # Check count
    count = db.query(models.StockData).filter(models.StockData.ticker == target_ticker).count()
    print(f"Found {count} records to delete.")
    
    # Delete
    if count > 0:
        db.query(models.StockData).filter(models.StockData.ticker == target_ticker).delete()
        db.commit()
        print(f"Successfully deleted {count} records for {target_ticker}.")
    else:
        print("No records found (clean).")

except Exception as e:
    print(f"Error: {e}")
    db.rollback()
finally:
    db.close()
