import sys
from sqlalchemy import create_engine, text
from models import Candle

DB_URL = "postgresql://postgres:YOUR_POSTGRES_PASSWORD@localhost/stock_data"
engine = create_engine(DB_URL)

try:
    with engine.connect() as conn:
        print("Dropping old backup table to free up index namespace...")
        conn.execute(text("DROP TABLE IF EXISTS candles_old_20260703_164518 CASCADE;"))
        conn.commit()
        print("Old backup table dropped.")
        
    # Now create the actual candles table
    Candle.__table__.create(bind=engine, checkfirst=True)
    print("Successfully created 'candles' table! System is now safe for retention.")
    
except Exception as e:
    print(f"Error: {e}")
