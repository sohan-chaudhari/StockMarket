import urllib.parse
"""
Phase 1: Fast Historical Backfill (2015-2019 Daily Data)
=========================================================
Fetch 1-day candles directly from Angel One API

Advantages:
- FAST: 1 request covers entire year (250 trading days)
- Simple: No resampling needed
- Efficient: Minimal API calls

Usage:
    # Single stock
    python backfill_phase1_daily.py --ticker RELIANCE.NS
    
    # Multiple stocks
    python backfill_phase1_daily.py --tickers RELIANCE.NS,TCS.NS,INFY.NS
    
    # All stocks
    python backfill_phase1_daily.py --all --workers 10
"""

import argparse
import sys
import time
from datetime import datetime, date
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from angelone_service import angelone_service  # For token lookup
from historical_service import historical_service  # For historical data

# Load environment
load_dotenv()

# Database connection
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "YOUR_POSTGRES_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "stock_data")

DATABASE_URL = f"postgresql://{DB_USER}:{urllib.parse.quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(bind=engine)


class DailyBackfiller:
    """
    Fast backfill for daily candles (2015-2019)
    """
    
    # Phase 1 date range
    START_DATE = date(2015, 1, 1)
    END_DATE = date(2019, 12, 31)
    
    def __init__(self):
        self.stats = {
            'success': 0,
            'failed': 0,
            'total_candles': 0,
            'start_time': None
        }
    
    def fetch_daily_candles(self, ticker: str) -> List[Dict]:
        """
        Fetch ALL daily candles for 2015-2019 in ONE request
        
        Angel One can return ~1000 candles per request
        5 years × 250 trading days = ~1250 candles
        So we split into 2 requests: 2015-2017, 2018-2019
        """
        all_candles = []
        
        # Split into 2 batches for safety
        date_ranges = [
            (date(2015, 1, 1), date(2017, 12, 31)),  # 3 years
            (date(2018, 1, 1), date(2019, 12, 31))   # 2 years
        ]
        
        for from_date, to_date in date_ranges:
            candles = self._fetch_batch(ticker, from_date, to_date)
            all_candles.extend(candles)
            time.sleep(0.35)  # Rate limiting
        
        return all_candles
    
    def _fetch_batch(self, ticker: str, from_date: date, to_date: date) -> List[Dict]:
        """
        Fetch single batch of daily candles using historical_service
        """
        # Determine exchange
        exchange = "NSE" if ticker.endswith('.NS') else "BSE"
        ticker_clean = ticker.replace('.NS', '').replace('.BO', '')
        
        # Use historical_service for fetching
        candles = historical_service.get_historical_candles(
            ticker=ticker_clean,
            interval="ONE_DAY",
            from_date=from_date,
            to_date=to_date,
            exchange=exchange
        )
        
        return candles
    
    def store_candles(self, ticker: str, candles: List[Dict]):
        """
        Store daily candles in database
        """
        db = SessionLocal()
        
        try:
            # Get ticker_id
            result = db.execute(text("""
                SELECT ticker_id FROM tickers WHERE ticker = :ticker
            """), {"ticker": ticker})
            
            row = result.fetchone()
            if not row:
                raise ValueError(f"Ticker {ticker} not in database")
            
            ticker_id = row[0]
            
            # Batch insert
            values = []
            for candle in candles:
                values.append({
                    'time': candle['timestamp'],
                    'ticker_id': ticker_id,
                    'open': float(candle['open']),
                    'high': float(candle['high']),
                    'low': float(candle['low']),
                    'close': float(candle['close']),
                    'volume': int(candle['volume'])
                })
            
            # Insert (you can use either stock_data table or candles_1d)
            # For now, let's use the existing stock_data table
            if values:
                db.execute(text("""
                    INSERT INTO stock_data (ticker, date, open, high, low, close, volume)
                    VALUES (:ticker_symbol, :date, :open, :high, :low, :close, :volume)
                    ON CONFLICT (ticker, date) DO NOTHING
                """), [
                    {
                        'ticker_symbol': ticker,
                        'date': v['time'].date(),
                        'open': v['open'],
                        'high': v['high'],
                        'low': v['low'],
                        'close': v['close'],
                        'volume': v['volume']
                    }
                    for v in values
                ])
                
                db.commit()
        
        finally:
            db.close()
    
    def backfill_ticker(self, ticker: str) -> bool:
        """
        Complete backfill for one ticker
        """
        try:
            print(f"[FETCH] {ticker:20s}", end=" ", flush=True)
            
            # Resolve DB ticker first
            db_ticker = self._resolve_db_ticker(ticker)
            if not db_ticker:
                print(f"[ERROR] Ticker {ticker} not found in DB")
                self.stats['failed'] += 1
                return False
            
            # Fetch daily candles
            candles = self.fetch_daily_candles(ticker)
            
            if not candles:
                print(f"[WARN] No data")
                self.stats['failed'] += 1
                return False
            
            # Store in database using correct DB ticker
            self.store_candles(db_ticker, candles)
            
            print(f"[OK] {len(candles):4d} candles (2015-2019)")
            
            self.stats['success'] += 1
            self.stats['total_candles'] += len(candles)
            
            return True
            
        except Exception as e:
            print(f"[ERROR] {e}")
            self.stats['failed'] += 1
            return False

    def _resolve_db_ticker(self, ticker: str) -> str:
        """Resolve ticker name in database (handle suffixes)"""
        db = SessionLocal()
        try:
            # 1. Try exact match
            result = db.execute(text("SELECT ticker FROM tickers WHERE ticker = :t"), {"t": ticker})
            if row := result.fetchone():
                return row[0]
            
            # 2. Try without suffix (RELIANCE.NS -> RELIANCE)
            clean_ticker = ticker.split('.')[0]
            result = db.execute(text("SELECT ticker FROM tickers WHERE ticker = :t"), {"t": clean_ticker})
            if row := result.fetchone():
                return row[0]
                
            return None
        finally:
            db.close()
    
    def print_summary(self):
        """Print final statistics"""
        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
        
        print("\n" + "=" * 70)
        print("PHASE 1 BACKFILL COMPLETE")
        print("=" * 70)
        print(f"[OK] Success:       {self.stats['success']}")
        print(f"[FAIL] Failed:      {self.stats['failed']}")
        print(f"[DATA] Total candles: {self.stats['total_candles']:,}")
        print(f"[TIME] Elapsed:     {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        
        if self.stats['success'] > 0:
            print(f"[STAT] Avg per stock:  {self.stats['total_candles'] // self.stats['success']:,} candles")
            print(f"[STAT] Speed:          {self.stats['success'] / (elapsed/60):.1f} stocks/minute")
        
        print("=" * 70)


def get_all_tickers() -> List[str]:
    """Get all active tickers from database"""
    db = SessionLocal()
    try:
        result = db.execute(text("SELECT ticker FROM tickers WHERE is_active = TRUE"))
        return [row[0] for row in result]
    finally:
        db.close()


def estimate_time(num_stocks: int) -> dict:
    """
    Estimate backfill time for Phase 1
    
    Calculation:
    - 2 API calls per stock (2015-2017, 2018-2019)
    - 0.35s delay per call (rate limiting)
    - Total: ~0.7s per stock
    """
    time_per_stock = 0.7  # seconds
    total_seconds = num_stocks * time_per_stock
    total_minutes = total_seconds / 60
    
    candles_per_stock = 5 * 250 * 0.7  # 5 years × 250 days × 70% (market open)
    total_candles = num_stocks * candles_per_stock
    
    return {
        'num_stocks': num_stocks,
        'estimated_minutes': round(total_minutes, 1),
        'estimated_hours': round(total_minutes / 60, 2),
        'estimated_candles': int(total_candles),
        'candles_per_stock': int(candles_per_stock)
    }


def main():
    parser = argparse.ArgumentParser(description="Phase 1: Daily backfill (2015-2019)")
    parser.add_argument('--ticker', type=str, help='Single ticker')
    parser.add_argument('--tickers', type=str, help='Comma-separated tickers')
    parser.add_argument('--all', action='store_true', help='All tickers')
    parser.add_argument('--workers', type=int, default=10, help='Parallel workers (default: 10)')
    
    args = parser.parse_args()
    
    # Initialize Historical Service (uses separate credentials)
    print("\n[LOGIN] Logging in to Historical Data API...")
    if not historical_service.login():
        print("[ERROR] Historical API login failed")
        print("[INFO] Make sure HISTORICAL_* credentials are set in .env")
        sys.exit(1)
    
    # Also login to main service (for token lookup)
    if not angelone_service.login():
        print("[WARN] Main API login failed (needed for token lookup)")
        print("Continuing with historical service only...")
    
    # Determine tickers
    if args.ticker:
        tickers = [args.ticker]
    elif args.tickers:
        tickers = [t.strip() for t in args.tickers.split(',')]
    elif args.all:
        tickers = get_all_tickers()
    else:
        parser.print_help()
        sys.exit(1)
    
    # Show estimate
    estimate = estimate_time(len(tickers))
    
    print("\n" + "=" * 70)
    print("PHASE 1: DAILY DATA BACKFILL (2015-2019)")
    print("=" * 70)
    print(f"Tickers:          {estimate['num_stocks']}")
    print(f"Date range:       2015-01-01 to 2019-12-31")
    print(f"Estimated time:   {estimate['estimated_minutes']:.1f} minutes ({estimate['estimated_hours']:.2f} hours)")
    print(f"Expected candles: {estimate['estimated_candles']:,}")
    print(f"Workers:          {args.workers}")
    print("=" * 70)
    
    response = input("\nProceed? (y/n): ")
    if response.lower() != 'y':
        print("Cancelled.")
        sys.exit(0)
    
    # Run backfill
    bfiller = DailyBackfiller()
    bfiller.stats['start_time'] = datetime.now()
    
    print(f"\n[START] Starting backfill with {args.workers} workers...\n")
    
    if args.workers == 1:
        # Sequential
        for ticker in tickers:
            bfiller.backfill_ticker(ticker)
    else:
        # Parallel
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(bfiller.backfill_ticker, ticker): ticker
                for ticker in tickers
            }
            
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"[ERROR] Unexpected error for {ticker}: {e}")
    
    # Print summary
    bfiller.print_summary()
    
    print("\n[NEXT] Run Phase 2 for 2020-2026 data")
    print("   (Will be planned after Phase 1 completes)")


if __name__ == "__main__":
    main()
