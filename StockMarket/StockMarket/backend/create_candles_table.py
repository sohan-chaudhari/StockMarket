import sys
from sqlalchemy import create_engine
from models import Base, Candle

DB_URL = "postgresql://postgres:medikart%403145@localhost/stock_data"
engine = create_engine(DB_URL)

try:
    # This will create the table if it doesn't exist
    Candle.__table__.create(bind=engine, checkfirst=True)
    print("Successfully created 'candles' table!")
except Exception as e:
    print(f"Error: {e}")
