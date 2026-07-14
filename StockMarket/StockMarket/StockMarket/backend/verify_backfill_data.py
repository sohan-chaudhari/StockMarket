import urllib.parse
"""
Verify Backfill Data
Check if data was correctly inserted for Phase 1
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

def verify_data():
    engine = create_engine(DATABASE_URL)
    
    tickers = ['RELIANCE', 'TCS', 'INFY']
    
    with engine.connect() as connection:
        print("\n[VERIFY] Checking backfilled data (2015-2019):")
        print("-" * 60)
        print(f"{'Ticker':<15} {'Count':<10} {'Min Date':<12} {'Max Date':<12}")
        print("-" * 60)
        
        for t in tickers:
            result = connection.execute(text("""
                SELECT 
                    COUNT(*) as count,
                    MIN(date) as min_date,
                    MAX(date) as max_date
                FROM stock_data 
                WHERE ticker = :t
                  AND date BETWEEN '2015-01-01' AND '2019-12-31'
            """), {"t": t})
            
            row = result.fetchone()
            print(f"{t:<15} {row.count:<10} {str(row.min_date):<12} {str(row.max_date):<12}")
            
        print("-" * 60)

if __name__ == "__main__":
    verify_data()
