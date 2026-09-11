import urllib.parse
"""
Complete Backfill Script with Tiered Storage
==============================================
Orchestrates: RecursiveFetcher → TieredResampler → Database Storage

Usage:
    # Single stock test
    python backfill_complete.py --ticker RELIANCE.NS --test
    
    # Multiple stocks
    python backfill_complete.py --tickers RELIANCE.NS,TCS.NS,HDFCBANK.NS
    
    # All stocks
    python backfill_complete.py --all --workers 5
"""

import argparse
import sys
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from recursive_fetcher import RecursiveFetcher, estimate_fetch_time
from resampler import TieredResampler
from angelone_service import angelone_service

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


class CompleteBFiller:
    """
    Complete backfill orchestrator
    """
    
    def __init__(self):
        self.fetcher = RecursiveFetcher()
        self.stats = {
            'success': 0,
            'failed': 0,
            'total_candles_5m': 0,
            'total_candles_15m': 0,
            'total_candles_1h': 0,
            'total_candles_1d': 0
        }
    
    def backfill_ticker(self, ticker: str, test_mode: bool = False) -> bool:
        """
        Complete backfill for a single ticker
        
        Steps:
            1. Fetch all 5m candles (2015 → now)
            2. Split by date tiers
            3. Resample each tier
            4. Store in database
        """
        print(f"\n{'=' * 70}")
        print(f"BACKFILLING: {ticker}")
        print(f"{'=' * 70}\n")
        
        try:
            # Step 1: Fetch 5m candles
            target_date = date(2015, 1, 1)
            if test_mode:
                # Test mode: only fetch last 30 days
                target_date = date.today() - timedelta(days=30)
                print(f"⚠️  TEST MODE: Fetching only last 30 days")
            
            candles_5m = self.fetcher.fetch_ticker_history(ticker, target_date=target_date)
            
            if not candles_5m:
                print(f"❌ No data fetched for {ticker}")
                self.stats['failed'] += 1
                return False
            
            print(f"\n✅ Fetched {len(candles_5m):,} 5m candles")
            
            # Step 2: Apply tiered resampling
            print("\n🔄 Applying tiered resampling...")
            resampled_tiers = TieredResampler.apply_tiered_resampling(candles_5m)
            
            TieredResampler.print_tier_summary(resampled_tiers)
            
            # Step 3: Store in database
            print("\n💾 Storing in database...")
            self._store_all_tiers(ticker, resampled_tiers)
            
            # Update stats
            self.stats['success'] += 1
            self.stats['total_candles_5m'] += len(resampled_tiers['5m'])
            self.stats['total_candles_15m'] += len(resampled_tiers['15m'])
            self.stats['total_candles_1h'] += len(resampled_tiers['1h'])
            self.stats['total_candles_1d'] += len(resampled_tiers['1d'])
            
            print(f"\n✅ {ticker} backfill complete!")
            return True
            
        except Exception as e:
            print(f"\n❌ Error backfilling {ticker}: {e}")
            self.stats['failed'] += 1
            return False
    
    def _store_all_tiers(self, ticker: str, tiers: dict):
        """
        Store all tiers in candles_5m table
        (continuous aggregates will handle higher timeframes)
        """
        db = SessionLocal()
    
        try:
            # Get ticker_id
            result = db.execute(text("""
                SELECT ticker_id FROM tickers WHERE ticker = :ticker
            """), {"ticker": ticker})
            
            row = result.fetchone()
            if not row:
                raise ValueError(f"Ticker {ticker} not found in tickers table")
            
            ticker_id = row[0]
            
            # Combine all candles (since we store everything as 5m base)
            # But we only store the RESAMPLED versions for older data
            all_candles = []
            
            # Recent data: keep as 5m
            all_candles.extend(tiers['5m'])
            
            # Older data: store resampled versions
            # (we fake them as 5m timestamps for storage, but they're actually aggregated)
            all_candles.extend(tiers['15m'])
            all_candles.extend(tiers['1h'])
            all_candles.extend(tiers['1d'])
            
            # Batch insert
            if all_candles:
                self._batch_insert(db, ticker_id, all_candles)
                print(f"  ✅ Inserted {len(all_candles):,} candles")
            
        finally:
            db.close()
    
    def _batch_insert(self, db, ticker_id: int, candles: List[dict]):
        """
        Batch insert candles
        """
        values = []
        for candle in candles:
            timestamp = candle['timestamp']
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            
            values.append({
                'time': timestamp,
                'ticker_id': ticker_id,
                'open': float(candle['open']),
                'high': float(candle['high']),
                'low': float(candle['low']),
                'close': float(candle['close']),
                'volume': int(candle['volume'])
            })
        
        # Insert in batches of 1000
        batch_size = 1000
        for i in range(0, len(values), batch_size):
            batch = values[i:i + batch_size]
            
            db.execute(text("""
                INSERT INTO candles_5m (time, ticker_id, open, high, low, close, volume)
                VALUES (:time, :ticker_id, :open, :high, :low, :close, :volume)
                ON CONFLICT (ticker_id, time) DO NOTHING
            """), batch)
        
        db.commit()
        
    def print_summary(self):
        """Print final summary"""
        print("\n" + "=" * 70)
        print("BACKFILL COMPLETE")
        print("=" * 70)
        print(f"✅ Success: {self.stats['success']}")
        print(f"❌ Failed:  {self.stats['failed']}")
        print(f"\nCandles inserted:")
        print(f"  5m:  {self.stats['total_candles_5m']:,}")
        print(f"  15m: {self.stats['total_candles_15m']:,}")
        print(f"  1h:  {self.stats['total_candles_1h']:,}")
        print(f"  1d:  {self.stats['total_candles_1d']:,}")
        print(f"\nTotal: {sum([
            self.stats['total_candles_5m'],
            self.stats['total_candles_15m'],
            self.stats['total_candles_1h'],
            self.stats['total_candles_1d']
        ]):,} candles")
        print("=" * 70)


def get_all_tickers() -> List[str]:
    """Get all tickers from database"""
    db = SessionLocal()
    try:
        result = db.execute(text("SELECT ticker FROM tickers WHERE is_active = TRUE"))
        return [row[0] for row in result]
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Complete backfill with tiered storage")
    parser.add_argument('--ticker', type=str, help='Single ticker to backfill')
    parser.add_argument('--tickers', type=str, help='Comma-separated list of tickers')
    parser.add_argument('--all', action='store_true', help='Backfill all tickers')
    parser.add_argument('--test', action='store_true', help='Test mode (only last 30 days)')
    parser.add_argument('--workers', type=int, default=3, help='Parallel workers (default: 3)')
    
    args = parser.parse_args()
    
    # Initialize Angel One
    if not angelone_service.login():
        print("❌ Angel One login failed")
        sys.exit(1)
    
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
    if not args.test:
        estimate = estimate_fetch_time(
            from_date=date.today(),
            to_date=date(2015, 1, 1),
            num_stocks=len(tickers)
        )
        
        print("\n⏱️  BACKFILL ESTIMATE")
        print(f"Tickers: {len(tickers)}")
        print(f"Date range: 2015-01-01 → {date.today()}")
        print(f"Total batches: {estimate['total_batches']:,}")
        print(f"Estimated time: {estimate['estimated_hours']:.1f} hours")
        print(f"Expected candles: {estimate['estimated_candles']:,}")
        
        response = input("\nProceed? (y/n): ")
        if response.lower() != 'y':
            print("Cancelled.")
            sys.exit(0)
    
    # Run backfill
    bfiller = CompleteBFiller()
    
    if args.workers == 1:
        # Sequential
        for ticker in tickers:
            bfiller.backfill_ticker(ticker, test_mode=args.test)
    else:
        # Parallel
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(bfiller.backfill_ticker, ticker, args.test): ticker
                for ticker in tickers
            }
            
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"❌ Unexpected error for {ticker}: {e}")
    
    # Print summary
    bfiller.print_summary()


if __name__ == "__main__":
    main()
