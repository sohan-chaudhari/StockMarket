import database, models
from sqlalchemy.orm import Session
from datetime import datetime

def check_reliance_duplicate_dates():
    db = database.SessionLocal()
    try:
        ticker = "RELIANCE"
        for model in [models.IntradayCandle15Min, models.IntradayCandle5Min]:
            print(f"\nChecking {model.__tablename__} for {ticker}...")
            
            # Check March 10
            start_10 = datetime(2026, 3, 10, 0, 0)
            end_10 = datetime(2026, 3, 11, 0, 0)
            rows_10 = db.query(model).filter(
                model.ticker == ticker,
                model.timestamp >= start_10,
                model.timestamp < end_10
            ).order_by(model.timestamp).all()
            print(f"March 10: {len(rows_10)} rows")
            if rows_10:
                print(f"  First: {rows_10[0].timestamp}, Last: {rows_10[-1].timestamp}")
                print(f"  Sample Close (first 3): {[r.close for r in rows_10[:3]]}")

            # Check March 11
            start_11 = datetime(2026, 3, 11, 0, 0)
            end_11 = datetime(2026, 3, 12, 0, 0)
            rows_11 = db.query(model).filter(
                model.ticker == ticker,
                model.timestamp >= start_11,
                model.timestamp < end_11
            ).order_by(model.timestamp).all()
            print(f"March 11: {len(rows_11)} rows")
            if rows_11:
                print(f"  First: {rows_11[0].timestamp}, Last: {rows_11[-1].timestamp}")
                print(f"  Sample Close (first 3): {[r.close for r in rows_11[:3]]}")

            # Compare March 10 and 11 counts and values
            if rows_10 and rows_11:
                match_count = 0
                for r10, r11 in zip(rows_10, rows_11):
                    if r10.close == r11.close and r10.open == r11.open:
                        match_count += 1
                print(f"Rows matching exactly (OHLC): {match_count}")

    finally:
        db.close()

if __name__ == "__main__":
    check_reliance_duplicate_dates()
