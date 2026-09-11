import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DB_URL = "postgresql://postgres:YOUR_POSTGRES_PASSWORD@localhost/stock_data"
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    print("--- 1 DAY DATA (Legacy Table) ---")
    t1d = session.execute(text("SELECT count(DISTINCT ticker) FROM stock_data")).scalar()
    oldest_1d = session.execute(text("SELECT MIN(date) FROM stock_data")).scalar()
    newest_1d = session.execute(text("SELECT MAX(date) FROM stock_data")).scalar()
    print(f"Tickers: {t1d} | Oldest: {oldest_1d} | Newest: {newest_1d}")

    print("\n--- UNIFIED CANDLES TABLE (Future Storage) ---")
    unified = session.execute(text("SELECT count(*) FROM candles")).scalar()
    print(f"Total rows in unified 'candles' table: {unified}")
    if unified > 0:
        tfs = session.execute(text("SELECT timeframe, count(*) FROM candles GROUP BY timeframe")).fetchall()
        for tf in tfs:
            print(f" - {tf[0]}: {tf[1]} rows")

except Exception as e:
    print(f"Error: {e}")
finally:
    session.close()
