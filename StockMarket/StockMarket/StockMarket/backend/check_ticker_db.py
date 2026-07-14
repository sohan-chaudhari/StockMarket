import urllib.parse
"""
Check Ticker Check
Verify if specific tickers exist in the database
"""
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# Database connection
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "medikart@3145")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "stock_data")

DATABASE_URL = f"postgresql://{DB_USER}:{urllib.parse.quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def check_tickers():
    engine = create_engine(DATABASE_URL)
    
    tickers_to_check = ['RELIANCE', 'RELIANCE.NS', 'TCS', 'TCS.NS', 'INFY', 'INFY.NS']
    
    with engine.connect() as connection:
        print("\nChecking for tickers:")
        for t in tickers_to_check:
            result = connection.execute(text("SELECT ticker_id, ticker FROM tickers WHERE ticker = :t"), {"t": t})
            row = result.fetchone()
            if row:
                print(f"[OK] Found: {row.ticker} (ID: {row.ticker_id})")
            else:
                print(f"[FAIL] Not found: {t}")
        
        # Search for similar
        print("\nSearching for 'RELIANCE':")
        result = connection.execute(text("SELECT ticker_id, ticker FROM tickers WHERE ticker LIKE '%RELIANCE%' LIMIT 5"))
        for row in result:
             print(f"  - {row.ticker} (ID: {row.ticker_id})")

if __name__ == "__main__":
    check_tickers()
