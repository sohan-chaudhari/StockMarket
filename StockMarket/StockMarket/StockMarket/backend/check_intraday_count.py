from backend.database import SessionLocal
from sqlalchemy import text

def check_count():
    session = SessionLocal()
    try:
        result = session.execute(text("SELECT ticker, COUNT(*), MIN(timestamp), MAX(timestamp) FROM intraday_candles_5min GROUP BY ticker"))
        rows = result.fetchall()
        print("\n--- Intraday 5-Min Data Count ---")
        if not rows:
            print("Table is empty.")
        for row in rows:
            print(f"{row[0]}: {row[1]} candles")
            print(f"  Range: {row[2]} to {row[3]}")
        print("---------------------------------")
    except Exception as e:
        print(f"Error checking count: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    check_count()
