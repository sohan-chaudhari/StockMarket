from backend.database import SessionLocal
from backend.models import StockData, Holiday
from sqlalchemy import delete

db = SessionLocal()

# Get all holidays
holidays = [h.date for h in db.query(Holiday).all()]

if not holidays:
    print("No holidays found in DB. Aborting cleanup.")
else:
    # Delete stock data on holidays
    deleted = db.query(StockData).filter(StockData.date.in_(holidays)).delete(synchronize_session=False)
    db.commit()
    print(f"Deleted {deleted} invalid data points that were on holidays.")

db.close()
