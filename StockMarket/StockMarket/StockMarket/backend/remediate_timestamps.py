import database, models
from sqlalchemy.orm import Session
from datetime import datetime

def remediate_reliance_data():
    db = database.SessionLocal()
    ticker = "RELIANCE"
    try:
        # Delete data for March 1 to Now to ensure a clean re-fetch of the entire month
        start_date = datetime(2026, 3, 1, 0, 0)
        end_date = datetime.now()
        
        print(f"Cleaning up {ticker} intraday data from {start_date} to {end_date}...")
        
        for model in [models.IntradayCandle5Min, models.IntradayCandle15Min]:
            deleted = db.query(model).filter(
                model.ticker == ticker,
                model.timestamp >= start_date,
                model.timestamp < end_date
            ).delete()
            print(f"Deleted {deleted} rows from {model.__tablename__}")
            
        db.commit()
        print("Remediation complete. Database cleaned for RELIANCE.")
        
    except Exception as e:
        print(f"Remediation error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    remediate_reliance_data()
