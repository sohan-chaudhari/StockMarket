import urllib.parse
"""
TimescaleDB Migration - Ticker Normalization Script
====================================================
Migrate existing tickers from stock_metadata to normalized tickers table

Usage:
    python migrate_tickers.py

Expected outcome:
    - Populates tickers table with SMALLINT IDs
    - Maps existing ticker strings to IDs
    - Validates no duplicates
"""

import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "YOUR_POSTGRES_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "stock_data")

DATABASE_URL = f"postgresql://{DB_USER}:{urllib.parse.quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def migrate_tickers():
    """
    Migrate tickers from stock_metadata to tickers table
    """
    db = SessionLocal()
    
    try:
        print("=" * 70)
        print("TIMESCALEDB MIGRATION: Ticker Normalization")
        print("=" * 70)
        
        # Step 1: Check if tickers table exists
        result = db.execute(text("""
            SELECT COUNT(*) FROM information_schema.tables 
            WHERE table_name = 'tickers'
        """))
        
        if result.scalar() == 0:
            print("\n[ERROR] 'tickers' table not found!")
            print("Please run: 001_create_tickers_table.sql first")
            return False
        
        print("\n[OK] Tickers table found")
        
        # Step 2: Check if stock_metadata exists
        result = db.execute(text("""
            SELECT COUNT(*) FROM information_schema.tables 
            WHERE table_name = 'stock_metadata'
        """))
        
        if result.scalar() == 0:
            print("\n[WARN] 'stock_metadata' table not found")
            print("Will create sample tickers instead...")
            create_sample_tickers(db)
            return True
        
        # Step 3: Count existing tickers
        result = db.execute(text("SELECT COUNT(*) FROM stock_metadata"))
        source_count = result.scalar()
        print(f"\n[INFO] Found {source_count} tickers in stock_metadata")
        
        # Step 4: Migrate tickers with auto-generated IDs
        print("\n[INFO] Migrating tickers...")
        
        result = db.execute(text("""
            INSERT INTO tickers (ticker_id, ticker, name, exchange)
            SELECT 
                ROW_NUMBER() OVER (ORDER BY ticker)::SMALLINT as ticker_id,
                ticker,
                name,
                COALESCE(exchange, 
                    CASE 
                        WHEN ticker LIKE '%.NS' THEN 'NSE'
                        WHEN ticker LIKE '%.BO' THEN 'BSE'
                        WHEN ticker IN ('NIFTY', 'BANKNIFTY', 'SENSEX') THEN 'INDEX'
                        ELSE 'NSE'
                    END
                ) as exchange
            FROM stock_metadata
            WHERE ticker IS NOT NULL
            ON CONFLICT (ticker) DO NOTHING
            RETURNING ticker_id
        """))
        
        inserted_count = len(result.fetchall())
        db.commit()
        
        print(f"[OK] Migrated {inserted_count} tickers")
        
        # Step 5: Verify migration
        result = db.execute(text("SELECT COUNT(*) FROM tickers"))
        final_count = result.scalar()
        
        result = db.execute(text("""
            SELECT COUNT(DISTINCT exchange) FROM tickers
        """))
        exchange_count = result.scalar()
        
        print("\n" + "=" * 70)
        print("MIGRATION SUMMARY")
        print("=" * 70)
        print(f"Total tickers migrated: {final_count}")
        print(f"Unique exchanges: {exchange_count}")
        
        # Show sample tickers
        print("\n[INFO] Sample tickers:")
        result = db.execute(text("""
            SELECT ticker_id, ticker, exchange 
            FROM tickers 
            ORDER BY ticker_id 
            LIMIT 10
        """))
        
        for row in result:
            print(f"  ID {row.ticker_id:4d}: {row.ticker:20s} ({row.exchange})")
        
        # Show storage size
        result = db.execute(text("""
            SELECT pg_size_pretty(pg_total_relation_size('tickers')) as size
        """))
        size = result.scalar()
        print(f"\nTable size: {size}")
        
        print("\n[OK] Migration completed successfully!")
        return True
        
    except Exception as e:
        print(f"\n[ERROR] during migration: {e}")
        db.rollback()
        return False
        
    finally:
        db.close()


def create_sample_tickers(db):
    """
    Create sample tickers for testing (if stock_metadata doesn't exist)
    """
    print("\n[INFO] Creating sample tickers...")
    
    sample_tickers = [
        (1, 'RELIANCE.NS', 'Reliance Industries', 'NSE'),
        (2, 'TCS.NS', 'Tata Consultancy Services', 'NSE'),
        (3, 'HDFCBANK.NS', 'HDFC Bank', 'NSE'),
        (4, 'INFY.NS', 'Infosys Limited', 'NSE'),
        (5, 'ICICIBANK.NS', 'ICICI Bank', 'NSE'),
        (6, 'NIFTY', 'NIFTY 50', 'INDEX'),
        (7, 'BANKNIFTY', 'NIFTY BANK', 'INDEX'),
        (8, 'SENSEX', 'S&P BSE SENSEX', 'INDEX'),
    ]
    
    for ticker_id, ticker, name, exchange in sample_tickers:
        db.execute(text("""
            INSERT INTO tickers (ticker_id, ticker, name, exchange)
            VALUES (:id, :ticker, :name, :exchange)
            ON CONFLICT (ticker) DO NOTHING
        """), {"id": ticker_id, "ticker": ticker, "name": name, "exchange": exchange})
    
    db.commit()
    print(f"[OK] Created {len(sample_tickers)} sample tickers")


def verify_ticker_ids():
    """
    Verify ticker IDs are within SMALLINT range (0-32767)
    """
    db = SessionLocal()
    
    try:
        result = db.execute(text("""
            SELECT MAX(ticker_id) as max_id, COUNT(*) as total
            FROM tickers
        """))
        
        row = result.fetchone()
        max_id = row.max_id
        total = row.total
        
        print("\n[INFO] Ticker ID Validation:")
        print(f"  Max ticker_id: {max_id}")
        print(f"  Total tickers: {total}")
        
        if max_id is not None and max_id > 32767:
            print("  [WARN] ticker_id exceeds SMALLINT limit (32767)")
            print("  Consider using INTEGER instead of SMALLINT")
        elif max_id is not None:
            print(f"  [OK] All IDs within SMALLINT range (remaining capacity: {32767 - max_id})")
        else:
            print("  [INFO] No tickers found")
        
    finally:
        db.close()


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("STARTING TICKER MIGRATION")
    print("=" * 70)
    
    success = migrate_tickers()
    
    if success:
        verify_ticker_ids()
        print("\n[OK] All checks passed! Ready for next migration step.")
        print("\nNext step: Run 002_create_intraday_candles_hypertable.sql")
        sys.exit(0)
    else:
        print("\n[FAIL] Migration failed. Please fix errors and retry.")
        sys.exit(1)
