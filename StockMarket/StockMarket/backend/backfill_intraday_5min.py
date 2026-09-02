import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, engine
from historical_service import HistoricalDataService
from angelone_service import angelone_service

# Initialize Services
historical_service = HistoricalDataService()

# Constants
START_DATE = datetime(2025, 12, 20)
END_DATE = datetime.now()
CHUNK_DAYS = 60 # Fetch in 60-day chunks to avoid API limits
MAX_WORKERS = 10 

def get_db_session():
    return SessionLocal()

def fetch_and_store_ticker(ticker, session):
    """
    Fetches 5-min data for a ticker in chunks and stores it.
    """
    total_inserted = 0
    current_start = START_DATE
    
    # Resolve ticker name for API (add suffix if needed)
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    
    while current_start < END_DATE:
        current_end = min(current_start + timedelta(days=CHUNK_DAYS), END_DATE)
        
        try:
            # Fetch Data
            candles = historical_service.get_historical_candles(
                ticker=clean_ticker,
                interval="FIVE_MINUTE",
                from_date=current_start,
                to_date=current_end,
                exchange="NSE" # Default to NSE for now
            )
            
            if candles:
                valid_candles = []
                for c in candles:
                    try:
                        # c['timestamp'] is already a datetime object with timezone
                        ts = c['timestamp']
                        
                        # Remove timezone info for DB storage if column is strictly timestamp without timezone
                        if ts.tzinfo is not None:
                            ts = ts.replace(tzinfo=None)

                        valid_candles.append({
                            "ticker": clean_ticker,
                            "timestamp": ts,
                            "open": float(c['open']),
                            "high": float(c['high']),
                            "low": float(c['low']),
                            "close": float(c['close']),
                            "volume": int(c['volume'])
                        })
                    except Exception as parse_err:
                        print(f"[{clean_ticker}] Parse error: {parse_err}")
                        continue

                if valid_candles:
                    # Bulk Insert — DO NOTHING so existing candles are never overwritten by the backfill
                    stmt = text("""
                        INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, is_backfilled, data_source)
                        VALUES (:ticker, '5m', :timestamp, :open, :high, :low, :close, :volume, TRUE, TRUE, 'BACKFILL')
                        ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
                    """)
                    
                    session.execute(stmt, valid_candles)
                    session.commit()
                    total_inserted += len(valid_candles)
                    
        except Exception as e:
            print(f"[{clean_ticker}] Error fetching {current_start.date()} to {current_end.date()}: {e}")
            session.rollback()
        
        # Move to next chunk
        current_start = current_end + timedelta(days=1)
    
    return total_inserted

def process_single_ticker(ticker):
    # Each thread gets its own session
    session = SessionLocal()
    try:
        count = fetch_and_store_ticker(ticker, session)
        return count
    finally:
        session.close()

def main():
    parser = argparse.ArgumentParser(description="Backfill 5-min Intraday Data")
    parser.add_argument("--ticker", type=str, help="Process a single ticker")
    parser.add_argument("--limit", type=int, help="Limit number of tickers to process")
    parser.add_argument("--workers", type=int, default=10, help="Number of worker threads")
    parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()

    print("=== Phase 2: Intraday 5-Min Backfill ===")
    
    # Login
    if not historical_service.login():
        print("Failed to login to Historical API. Exiting.")
        sys.exit(1)

    # LOAD INSTRUMENTS (Fix for missing tokens)
    print("[Backfill] Loading Instruments...")
    angelone_service.load_instruments()
    
    # Update START_DATE
    global START_DATE
    if args.start_date:
        try:
            START_DATE = datetime.strptime(args.start_date, "%Y-%m-%d")
            print(f"Using start date: {START_DATE}")
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD.")
            sys.exit(1)

    session = get_db_session()
    
    tickers = []
    if args.ticker:
        tickers = [args.ticker]
    else:
        # Get Tickers
        print("Fetching tickers from Database...")
        res = session.execute(text("SELECT ticker FROM stock_metadata"))
        tickers = [row[0] for row in res.fetchall()]
    
    session.close() # Close main session
    
    unique_tickers = sorted(list(set(tickers)))
    
    if args.limit:
        unique_tickers = unique_tickers[:args.limit]
        
    print(f"Found {len(unique_tickers)} tickers to process.")
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_ticker = {}
        for ticker in unique_tickers:
            future_to_ticker[executor.submit(process_single_ticker, ticker)] = ticker
            
        completed = 0
        total = len(unique_tickers)
        
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                count = future.result()
                completed += 1
                if count is not None and count > 0:
                    print(f"[{completed}/{total}] {ticker}: Synced {count} candles")
                else:
                    print(f"[{completed}/{total}] {ticker}: No data or skipped")
            except Exception as exc:
                print(f"[{completed}/{total}] {ticker} generated an exception: {exc}")

    print(f"\nBackfill Completed in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()

