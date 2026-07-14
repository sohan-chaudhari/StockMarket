import os
import sys
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text
from database import SessionLocal
from historical_service import HistoricalDataService
from angelone_service import angelone_service

# Initialize Services
historical_service = HistoricalDataService()

START_DATE = datetime.now() - timedelta(days=60)
END_DATE = datetime.now()
MAX_WORKERS = 8

def get_db_session():
    return SessionLocal()

def fetch_and_store_ticker(ticker, session):
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    total_inserted = 0
    
    try:
        # Fetch Data - single chunk for 60 days
        candles = historical_service.get_historical_candles(
            ticker=clean_ticker,
            interval="FIVE_MINUTE",
            from_date=START_DATE,
            to_date=END_DATE,
            exchange="NSE"
        )
        
        if candles:
            valid_candles = []
            for c in candles:
                try:
                    ts = c['timestamp']
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
                except Exception:
                    continue

            if valid_candles:
                stmt = text("""
                    INSERT INTO intraday_candles_5min (ticker, timestamp, open, high, low, close, volume)
                    VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                    ON CONFLICT (ticker, timestamp) DO UPDATE SET
                        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                        close = EXCLUDED.close, volume = EXCLUDED.volume;
                """)
                
                # Insert in chunks of 500
                chunk_size = 500
                for i in range(0, len(valid_candles), chunk_size):
                    chunk = valid_candles[i:i+chunk_size]
                    session.execute(stmt, chunk)
                session.commit()
                total_inserted = len(valid_candles)
                
    except Exception as e:
        print(f"[{clean_ticker}] Error: {e}")
        session.rollback()
        
    return total_inserted

def process_single_ticker(ticker):
    session = SessionLocal()
    try:
        return fetch_and_store_ticker(ticker, session)
    finally:
        session.close()

def main():
    print("=== Fetching 2-Month 5m Candles for Remaining Tickers ===")
    
    if not historical_service.login():
        print("Failed to login to Historical API. Exiting.")
        sys.exit(1)

    print("Loading Instruments...")
    angelone_service.load_instruments()
    
    session = get_db_session()
    
    # 1. Get all tickers
    try:
        res = session.execute(text("SELECT ticker FROM stock_metadata"))
        all_tickers = [row[0] for row in res.fetchall()]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        session.close()
        return

    # 2. Get counts per ticker in intraday_candles_5min
    print("Identifying missing tickers (having less than 50 rows of 5m data)...")
    res = session.execute(text("SELECT ticker, COUNT(*) FROM intraday_candles_5min GROUP BY ticker"))
    counts = {row[0]: row[1] for row in res.fetchall()}
    session.close()

    missing_tickers = []
    for t in all_tickers:
        clean_t = t.replace('.NS', '').replace('.BO', '')
        if counts.get(clean_t, 0) < 50:
            missing_tickers.append(t)
            
    print(f"Found {len(missing_tickers)} tickers missing 5m data out of {len(all_tickers)} total.")
    
    if not missing_tickers:
        print("All tickers have sufficient 5m data.")
        return

    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_ticker = {executor.submit(process_single_ticker, t): t for t in missing_tickers}
        
        completed = 0
        total = len(missing_tickers)
        
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                count = future.result()
                completed += 1
                if count and count > 0:
                    print(f"[{completed}/{total}] {ticker}: Synced {count} candles")
                else:
                    print(f"[{completed}/{total}] {ticker}: No data")
            except Exception as exc:
                completed += 1
                print(f"[{completed}/{total}] {ticker} generated an exception: {exc}")

    print(f"\nFetch Completed in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
