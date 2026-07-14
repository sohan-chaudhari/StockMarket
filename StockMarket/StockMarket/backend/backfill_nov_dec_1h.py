"""
Bulk Backfill Script
====================
Fetches 1-hour candles for all active tickers for Nov-Dec 2025 using Angel One API.
Inserts into the unified `candles` table.

Usage:
    python backfill_nov_dec_1h.py --workers 10
"""

import argparse
import sys
import time
from datetime import datetime, date
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import SessionLocal
from models import Candle
from angelone_service import angelone_service
from historical_service import historical_service

class OneHourBackfiller:
    def __init__(self):
        self.stats = {
            'success': 0,
            'failed': 0,
            'total_candles': 0,
            'start_time': None
        }

    def fetch_1h_candles(self, ticker: str) -> List[Dict]:
        """Fetch 1h candles for Nov and Dec 2025"""
        exchange = "NSE" if ticker.endswith('.NS') else "BSE"
        ticker_clean = ticker.replace('.NS', '').replace('.BO', '')
        
        candles = historical_service.get_historical_candles(
            ticker=ticker_clean,
            interval="ONE_HOUR",
            from_date=date(2025, 11, 1),
            to_date=date(2025, 12, 31),
            exchange=exchange
        )
        return candles

    def store_candles(self, ticker: str, candles: List[Dict]):
        """Store 1h candles safely in the database"""
        if not candles:
            return

        db = SessionLocal()
        try:
            values = []
            for candle in candles:
                ts = candle['timestamp']
                # Strip timezone info if present since DB might expect naive timestamps
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                
                values.append({
                    'ticker': ticker,
                    'timeframe': '1h',
                    'timestamp': ts,
                    'open': float(candle['open']),
                    'high': float(candle['high']),
                    'low': float(candle['low']),
                    'close': float(candle['close']),
                    'volume': int(candle['volume']),
                    'is_completed': True,
                    'data_source': 'ANGELONE',
                    'is_backfilled': True
                })

            # Batch insert using PostgreSQL dialect for ON CONFLICT DO NOTHING
            stmt = pg_insert(Candle).values(values)
            stmt = stmt.on_conflict_do_nothing(constraint="uix_candle_key")
            
            db.execute(stmt)
            db.commit()
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def process_ticker(self, ticker: str) -> bool:
        """Process a single ticker"""
        try:
            print(f"[FETCH] {ticker:20s}", end=" ", flush=True)
            
            candles = self.fetch_1h_candles(ticker)
            
            if not candles:
                print("[WARN] No data")
                self.stats['failed'] += 1
                return False
                
            self.store_candles(ticker, candles)
            
            print(f"[OK] {len(candles):4d} candles")
            
            self.stats['success'] += 1
            self.stats['total_candles'] += len(candles)
            return True
            
        except Exception as e:
            print(f"[ERROR] {e}")
            self.stats['failed'] += 1
            return False

    def print_summary(self):
        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
        print("\n" + "=" * 70)
        print("BACKFILL COMPLETE")
        print("=" * 70)
        print(f"[OK] Success:       {self.stats['success']}")
        print(f"[FAIL] Failed:      {self.stats['failed']}")
        print(f"[DATA] Total candles: {self.stats['total_candles']:,}")
        print(f"[TIME] Elapsed:     {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print("=" * 70)


def get_all_active_tickers() -> List[str]:
    db = SessionLocal()
    try:
        # Based on stock_metadata table
        result = db.execute(text("SELECT ticker FROM stock_metadata WHERE is_active = TRUE"))
        return [row[0] for row in result]
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill 1h candles for Nov-Dec 2025")
    parser.add_argument('--workers', type=int, default=10, help='Parallel workers (default: 10)')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of tickers for testing')
    args = parser.parse_args()

    print("\n[LOGIN] Logging in to Historical Data API...")
    if not historical_service.login():
        print("[ERROR] Historical API login failed")
        sys.exit(1)
        
    if not angelone_service.login():
        print("[WARN] Main API login failed, might affect token lookup")

    tickers = get_all_active_tickers()
    if args.limit > 0:
        tickers = tickers[:args.limit]

    print(f"\nFound {len(tickers)} active tickers.")
    
    bfiller = OneHourBackfiller()
    bfiller.stats['start_time'] = datetime.now()
    
    print(f"\n[START] Starting backfill with {args.workers} workers...\n")
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(bfiller.process_ticker, ticker): ticker for ticker in tickers}
        
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"[ERROR] Unexpected error for {ticker}: {e}")
                
            # Basic rate limit wait between triggering (in thread pool this just slows the completion loop, 
            # but workers will also naturally rate limit by network IO. We can add sleep in the worker if needed)
            time.sleep(0.35 / args.workers)

    bfiller.print_summary()


if __name__ == "__main__":
    main()
