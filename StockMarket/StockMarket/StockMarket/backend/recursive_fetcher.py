"""
Recursive Batch Fetcher for Angel One Historical Data
=======================================================
Fetches 5-minute candles recursively from latest to 2015

Features:
- Handles Angel One 1000-candle limit
- Batch fetching in ~13-day chunks
- Rate limiting (3 req/sec)
- Checkpoint resume on failure
- Progress tracking
"""

import time
from datetime import datetime, timedelta, date, time as dt_time
from typing import List, Dict, Optional
import json
import os
from angelone_service import angelone_service


class RecursiveFetcher:
    """
    Fetch 5-min candles recursively from Angel One API
    """
    
    # Angel One constraints
    MAX_CANDLES_PER_REQUEST = 1000
    CANDLES_PER_DAY_5M = 75  # 6.25 hours × 12 candles/hour
    BATCH_SIZE_DAYS = 13     # ~975 candles per batch
    RATE_LIMIT_DELAY = 0.35   # ~3 requests/second
    
    # Market hours
    MARKET_OPEN = dt_time(9, 15)
    MARKET_CLOSE = dt_time(15, 30)
    
    def __init__(self, checkpoint_file: str = "fetch_checkpoint.json"):
        self.checkpoint_file = checkpoint_file
        self.stats = {
            'total_batches': 0,
            'total_candles': 0,
            'failed_batches': 0,
            'start_time': None,
            'end_time': None
        }
    
    def fetch_ticker_history(
        self, 
        ticker: str, 
        to_date: datetime = None,
        target_date: date = date(2015, 1, 1)
    ) -> List[Dict]:
        """
        Fetch all 5m candles from to_date back to target_date
        
        Args:
            ticker: Stock symbol (e.g., 'RELIANCE.NS')
            to_date: End date (default: now)
            target_date: Earliest date to fetch (default: 2015-01-01)
        
        Returns:
            List of candle dictionaries
        """
        if to_date is None:
            to_date = datetime.now()
        
        self.stats['start_time'] = datetime.now()
        
        print(f"\n{'=' * 70}")
        print(f"RECURSIVE FETCH: {ticker}")
        print(f"{'=' * 70}")
        print(f"From: {to_date.date()}")
        print(f"To:   {target_date}")
        print(f"Batch size: {self.BATCH_SIZE_DAYS} days (~{self.BATCH_SIZE_DAYS * self.CANDLES_PER_DAY_5M} candles)")
        print(f"{'=' * 70}\n")
        
        all_candles = []
        current_to = to_date
        iteration = 0
        
        # Load checkpoint if exists
        checkpoint = self._load_checkpoint(ticker)
        if checkpoint:
            current_to = datetime.fromisoformat(checkpoint['last_from_date'])
            all_candles = checkpoint['candles']
            iteration = checkpoint['iteration']
            print(f"📂 Resumed from checkpoint: iteration {iteration}, date {current_to.date()}")
        
        while current_to.date() > target_date:
            iteration += 1
            
            # Calculate from_date for this batch
            from_date = current_to - timedelta(days=self.BATCH_SIZE_DAYS)
            
            # Ensure we don't go before target
            if from_date.date() < target_date:
                from_date = datetime.combine(target_date, self.MARKET_OPEN)
            
            # Ensure from_date is during market hours
            from_date = self._adjust_to_market_hours(from_date)
            current_to_adjusted = self._adjust_to_market_hours(current_to)
            
            print(f"Batch {iteration:3d}: {from_date.date()} → {current_to_adjusted.date()}", 
                  end=" ", flush=True)
            
            try:
                # Fetch batch from Angel One
                candles = self._fetch_batch(ticker, from_date, current_to_adjusted)
                
                if candles:
                    all_candles.extend(candles)
                    self.stats['total_candles'] += len(candles)
                    print(f"✅ {len(candles):4d} candles")
                else:
                    print(f"⚠️  No data (holiday/weekend?)")
                
                self.stats['total_batches'] += 1
                
                # Save checkpoint every 10 batches
                if iteration % 10 == 0:
                    self._save_checkpoint(ticker, {
                        'iteration': iteration,
                        'last_from_date': from_date.isoformat(),
                        'candles': all_candles,
                        'stats': self.stats
                    })
                
            except Exception as e:
                print(f"❌ Error: {e}")
                self.stats['failed_batches'] += 1
                
                # Save checkpoint on error
                self._save_checkpoint(ticker, {
                    'iteration': iteration,
                    'last_from_date': from_date.isoformat(),
                    'candles': all_candles,
                    'stats': self.stats,
                    'last_error': str(e)
                })
                
                # Continue to next batch
            
            # Move to next batch
            current_to = from_date
            
            # Rate limiting
            time.sleep(self.RATE_LIMIT_DELAY)
        
        self.stats['end_time'] = datetime.now()
        
        # Print summary
        self._print_summary()
        
        # Clean up checkpoint on success
        self._cleanup_checkpoint(ticker)
        
        return all_candles
    
    def _fetch_batch(
        self, 
        ticker: str, 
        from_date: datetime, 
        to_date: datetime
    ) -> List[Dict]:
        """
        Fetch a single batch from Angel One API
        
        Returns:
            List of candle dictionaries
        """
        # Get token for ticker
        ticker_clean = ticker.replace('.NS', '').replace('.BO', '')
        token_data = angelone_service.get_token(ticker_clean)
        
        if not token_data:
            raise ValueError(f"Ticker not found in Angel One: {ticker}")
        
        # Prepare API params
        params = {
            "exchange": "NSE" if ticker.endswith('.NS') else "BSE",
            "symboltoken": token_data['token'],
            "interval": "FIVE_MINUTE",
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M")
        }
        
        # Call Angel One API
        response = angelone_service.smart_api.getCandleData(params)
        
        if response['status'] and response.get('data'):
            candles = []
            for candle in response['data']:
                # Angel One format: [timestamp, open, high, low, close, volume]
                candles.append({
                    'timestamp': datetime.strptime(candle[0], "%Y-%m-%dT%H:%M:%S%z"),
                    'open': float(candle[1]),
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4]),
                    'volume': int(candle[5]) if candle[5] else 0
                })
            return candles
        
        elif response['status'] is False:
            error_msg = response.get('message', 'Unknown error')
            # If "no data" error, return empty list (not a failure)
            if 'no data' in error_msg.lower():
                return []
            raise ValueError(f"Angel One API error: {error_msg}")
        
        return []
    
    def _adjust_to_market_hours(self, dt: datetime) -> datetime:
        """
        Adjust datetime to market hours (9:15 AM - 3:30 PM)
        """
        if dt.time() < self.MARKET_OPEN:
            return datetime.combine(dt.date(), self.MARKET_OPEN)
        elif dt.time() > self.MARKET_CLOSE:
            return datetime.combine(dt.date(), self.MARKET_CLOSE)
        return dt
    
    def _save_checkpoint(self, ticker: str, data: dict):
        """Save progress checkpoint"""
        checkpoint_path = f"{ticker}_{self.checkpoint_file}"
        with open(checkpoint_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _load_checkpoint(self, ticker: str) -> Optional[dict]:
        """Load progress checkpoint if exists"""
        checkpoint_path = f"{ticker}_{self.checkpoint_file}"
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path, 'r') as f:
                return json.load(f)
        return None
    
    def _cleanup_checkpoint(self, ticker: str):
        """Remove checkpoint file after successful completion"""
        checkpoint_path = f"{ticker}_{self.checkpoint_file}"
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
    
    def _print_summary(self):
        """Print fetch statistics"""
        elapsed = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
        
        print(f"\n{'=' * 70}")
        print("FETCH SUMMARY")
        print(f"{'=' * 70}")
        print(f"Total batches:     {self.stats['total_batches']}")
        print(f"Failed batches:    {self.stats['failed_batches']}")
        print(f"Total candles:     {self.stats['total_candles']:,}")
        print(f"Time elapsed:      {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"Candles/second:    {self.stats['total_candles']/elapsed:.1f}")
        print(f"{'=' * 70}\n")


# ============================================================================
# Estimated Time Calculator
# ============================================================================

def estimate_fetch_time(
    from_date: date = date.today(),
    to_date: date = date(2015, 1, 1),
    num_stocks: int = 1
) -> dict:
    """
    Estimate time required to fetch historical data
    
    Returns:
        Dictionary with estimates
    """
    days_range = (from_date - to_date).days
    batches_per_stock = days_range / RecursiveFetcher.BATCH_SIZE_DAYS
    total_batches = batches_per_stock * num_stocks
    
    # Time per batch (fetch + rate limit)
    time_per_batch = 0.5 + RecursiveFetcher.RATE_LIMIT_DELAY  # ~0.85 seconds
    
    total_seconds = total_batches * time_per_batch
    total_hours = total_seconds / 3600
    
    candles_per_stock = days_range * RecursiveFetcher.CANDLES_PER_DAY_5M * 0.7  # 70% market days
    total_candles = candles_per_stock * num_stocks
    
    return {
        'days_range': days_range,
        'batches_per_stock': int(batches_per_stock),
        'total_batches': int(total_batches),
        'estimated_hours': round(total_hours, 2),
        'estimated_candles': int(total_candles),
        'candles_per_stock': int(candles_per_stock)
    }


# ============================================================================
# Usage Example
# ============================================================================

if __name__ == "__main__":
    # Example: Fetch RELIANCE from 2015 to now
    
    # Initialize Angel One
    if not angelone_service.login():
        print("❌ Angel One login failed")
        exit(1)
    
    # Estimate time
    estimate = estimate_fetch_time(
        from_date=date.today(),
        to_date=date(2015, 1, 1),
        num_stocks=1
    )
    
    print("\n⏱️  FETCH ESTIMATE")
    print(f"Days range: {estimate['days_range']}")
    print(f"Batches: {estimate['batches_per_stock']}")
    print(f"Estimated time: {estimate['estimated_hours']:.2f} hours")
    print(f"Expected candles: {estimate['estimated_candles']:,}")
    
    # Fetch data
    fetcher = RecursiveFetcher()
    candles = fetcher.fetch_ticker_history('RELIANCE.NS')
    
    print(f"\n✅ Fetched {len(candles):,} candles for RELIANCE.NS")
    
    # Show sample
    if candles:
        print("\nFirst 5 candles:")
        for candle in candles[:5]:
            print(f"  {candle['timestamp']} | O:{candle['open']} H:{candle['high']} "
                  f"L:{candle['low']} C:{candle['close']} V:{candle['volume']}")
