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

START_DATE = datetime.now() - timedelta(days=30)
END_DATE = datetime.now()
MAX_WORKERS = 2

def get_db_session():
    return SessionLocal()

def fetch_and_store_ticker(ticker, session):
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    total_inserted = 0
    time.sleep(0.3)
    
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
    
    historical_service.login()
    time.sleep(2)
    if not historical_service.is_logged_in:
        print("[ERROR] Failed to login to Angel One API.")
        return

    session = get_db_session()
    try:
        res = session.execute(text("SELECT ticker FROM stock_metadata"))
        all_tickers = [row[0] for row in res.fetchall()]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        session.close()
        return
    finally:
        session.close()

    total = len(all_tickers)
    print(f"Found {total} tickers to backfill last 30 days of 5m candles.")
    start_time = time.time()

    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_and_store_ticker, t, get_db_session()): t for t in all_tickers}
        
        for future in as_completed(futures):
            ticker = futures[future]
            completed += 1
            try:
                inserted = future.result()
                if inserted > 0:
                    print(f"[{completed}/{total}] {ticker}: Synced {inserted} candles")
                else:
                    print(f"[{completed}/{total}] {ticker}: No data")
            except Exception as exc:
                print(f"[{completed}/{total}] {ticker} generated an exception: {exc}")

    print(f"\nFetch Completed in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
