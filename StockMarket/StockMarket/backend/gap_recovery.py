import time
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from historical_service import historical_service
from database import get_ist_now
from exchange_calendar import IST, get_market_status

class GapRecoveryService:
    def __init__(self):
        # Prevent spamming the API: Track last attempt time per ticker
        # { "TICKER": timestamp_of_last_attempt }
        self._last_attempt = {}
        # Cooldown in seconds before trying to recover the same ticker again
        self._attempt_cooldown = 60  

    def recover_5m_gaps(self, ticker: str, last_stored_ts: Optional[int], db: Session) -> bool:
        """
        Check if there's a gap > 10m for the 5m timeframe and fetch missing candles.
        Returns True if a gap was detected and recovery was attempted (or succeeded).
        """
        now = datetime.now(IST).replace(tzinfo=None)
        
        # We only care about gap recovery during or after market hours on a trading day.
        # But to keep it simple, we just check the physical time gap.
        
        if last_stored_ts is None:
            # No data at all? Don't use gap recovery for entire history. 
            # Background daily sync should handle brand new tickers.
            return False
            
        last_dt = datetime.fromtimestamp(last_stored_ts)
        
        # If the gap is less than 10 minutes (2 candles), it's not a gap.
        # 5 minutes is normal because the current 5m candle is forming.
        gap_seconds = (now - last_dt).total_seconds()
        if gap_seconds <= 600:
            return False
            
        # Check cooldown
        last_attempt = self._last_attempt.get(ticker, 0)
        if time.time() - last_attempt < self._attempt_cooldown:
            return False
            
        self._last_attempt[ticker] = time.time()
        
        # Calculate exactly what to fetch
        # AngelOne Historical API wants IST datetimes
        # We'll fetch from the last_stored_ts up to now
        start_fetch = last_dt
        end_fetch = now
        
        print(f"[GapRecovery] Detected gap for {ticker}. Fetching 5m candles from {start_fetch.strftime('%H:%M')} to {end_fetch.strftime('%H:%M')}")
        
        try:
            if not historical_service.is_logged_in:
                historical_service.login()
                
            if not historical_service.is_logged_in:
                print(f"[GapRecovery] Failed because historical_service is not logged in.")
                return False
                
            fetched_candles = historical_service.get_historical_candles(
                ticker=ticker,
                interval="FIVE_MINUTE",
                from_date=start_fetch,
                to_date=end_fetch
            )
            
            if not fetched_candles:
                print(f"[GapRecovery] AngelOne returned no new candles for {ticker}.")
                return True # Attempted
                
            # Insert into database
            from models import Candle
            
            added = 0
            for c in fetched_candles:
                c_ts = c['timestamp'].replace(tzinfo=None)
                # Ensure we strictly only insert candles newer than our last stored one
                if c_ts <= last_dt:
                    continue
                    
                # Create Candle model
                new_candle = Candle(
                    ticker=ticker,
                    timeframe="5m",
                    timestamp=c_ts,
                    open=c['open'],
                    high=c['high'],
                    low=c['low'],
                    close=c['close'],
                    volume=c['volume'],
                    is_completed=True,
                    data_source="ANGELONE",
                    is_backfilled=True
                )
                db.merge(new_candle) # merge handles unique constraint (updates if exists, though it shouldn't)
                added += 1
                
            if added > 0:
                db.commit()
                print(f"[GapRecovery] Successfully recovered {added} missing 5m candles for {ticker}.")
            else:
                db.rollback()
                
            return True
            
        except Exception as e:
            print(f"[GapRecovery] Error recovering candles for {ticker}: {e}")
            db.rollback()
            return False

gap_recovery_service = GapRecoveryService()
