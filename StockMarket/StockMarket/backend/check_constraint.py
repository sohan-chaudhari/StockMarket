import sys
from sqlalchemy import create_engine, text

DB_URL = "postgresql://postgres:YOUR_POSTGRES_PASSWORD@localhost/stock_data"
engine = create_engine(DB_URL)

try:
    with engine.connect() as conn:
        # Find which table has the constraint uix_candle_key
        sql = """
        SELECT conrelid::regclass AS table_name, conname
        FROM pg_constraint
        WHERE conname = 'uix_candle_key';
        """
        result = conn.execute(text(sql)).fetchall()
        print("Constraint found on tables:", result)
        
        # If it's on a legacy table we don't care about, we should drop the constraint or rename it
        # Or better yet, just create the candles table without the unique constraint temporarily just to see
except Exception as e:
    print(f"Error: {e}")
