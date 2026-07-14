import os
import sys
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text
from database import SessionLocal
from historical_service import historical_service
from angelone_service import angelone_service
from models import IntradayCandle30Min

historical_service.login()

START_DATE = datetime(2026, 1, 1, 9, 15)
END_DATE = datetime(2026, 2, 28, 15, 30)
MAX_WORKERS = 2

def get_db_session():
    return SessionLocal()

def fetch_and_store_ticker(ticker, session):
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    total_inserted = 0
    time.sleep(0.35) # strict API rate limit logic
    
    try:
        # Fetch Data
        candles = historical_service.get_historical_candles(
            ticker=clean_ticker,
            interval="THIRTY_MINUTE",
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
                    INSERT INTO intraday_candles_30min (ticker, timestamp, open, high, low, close, volume)
                    VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                    ON CONFLICT (ticker, timestamp) DO NOTHING
                """)
                
                chunk_size = 500
                for i in range(0, len(valid_candles), chunk_size):
                    chunk = valid_candles[i:i+chunk_size]
                    session.execute(stmt, chunk)
                session.commit()
                total_inserted = len(valid_candles)
                
    except Exception as e:
        print(f"[ERROR] {ticker}: {e}")
        session.rollback()
        
    return clean_ticker, total_inserted

def main():
    db = get_db_session()
    print("Fetching tickers from DB...")
    rows = db.execute(text("SELECT ticker FROM stock_metadata")).fetchall()
    tickers = [r[0] for r in rows]
    db.close()
    
    total = len(tickers)
    print(f"Starting fetch for {total} tickers for 30m Jan/Feb...")
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_and_store_ticker, t, get_db_session()): t for t in tickers}
        
        completed = 0
        for future in as_completed(futures):
            t = futures[future]
            completed += 1
            try:
                ticker, count = future.result()
                if count > 0:
                    print(f"[{completed}/{total}] {ticker}: Synced {count} candles")
                else:
                    print(f"[{completed}/{total}] {ticker}: No data")
            except Exception as e:
                print(f"[{completed}/{total}] {t}: Error {e}")
                
    end_time = time.time()
    print(f"\nFetch Completed in {end_time - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
