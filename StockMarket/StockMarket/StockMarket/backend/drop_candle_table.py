
import sys
import os
from sqlalchemy import text

# Ensure backend module can be found
current_dir = os.path.dirname(os.path.abspath(__file__)) # e:\StockMarket\backend
root_dir = os.path.dirname(current_dir) # e:\StockMarket
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from backend.database import engine

def drop_candle_table():
    try:
        with engine.connect() as conn:
            print("Dropping current_day_candle table to force schema update...")
            conn.execute(text("DROP TABLE IF EXISTS current_day_candle CASCADE"))
            conn.commit()
            print("Table dropped successfully.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    drop_candle_table()
