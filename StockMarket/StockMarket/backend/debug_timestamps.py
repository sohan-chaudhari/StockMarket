import database, models
from sqlalchemy.orm import Session
from datetime import datetime

def check_detailed_data():
    db = database.SessionLocal()
    try:
        ticker = "RELIANCE"
        for model in [models.IntradayCandle15Min]:
            print(f"\nChecking {model.__tablename__} timestamps...")
            
            # Check March 1 to now
            from collections import Counter
            start = datetime(2026, 3, 1, 0, 0)
            end = datetime(2026, 3, 17, 0, 0)
            rows = db.query(model).filter(
                model.ticker == ticker,
                model.timestamp >= start,
                model.timestamp < end
            ).order_by(model.timestamp).all()
            
            day_counts = Counter(r.timestamp.date() for r in rows)
            for day, count in sorted(day_counts.items()):
                print(f"Date: {day} | Count: {count}")
                # Sample the first timestamp for that day
                first_ts = next(r.timestamp for r in rows if r.timestamp.date() == day)
                print(f"  First TS: {first_ts}")

    finally:
        db.close()

if __name__ == "__main__":
    check_detailed_data()
