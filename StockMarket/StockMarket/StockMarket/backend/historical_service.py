"""
Angel One Historical Data Service
==================================
Separate service for fetching historical candle data
Uses dedicated credentials to avoid rate limit conflicts with live data

Usage:
    from historical_service import historical_service
    
    # Login
    historical_service.login()
    
    # Fetch daily candles
    candles = historical_service.get_historical_candles(
        ticker='RELIANCE',
        interval='ONE_DAY',
        from_date=date(2015, 1, 1),
        to_date=date(2019, 12, 31)
    )
"""

import os
from typing import Optional, List, Dict
from datetime import date, datetime
from SmartApi import SmartConnect
import pyotp
from dotenv import load_dotenv
import pathlib

# Load environment variables
backend_dir = pathlib.Path(__file__).parent.resolve()
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)


class HistoricalDataService:
    """
    Dedicated service for historical data fetching
    Separate from live price service to avoid rate limit conflicts
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(HistoricalDataService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # Load HISTORICAL credentials (separate from live)
        self.api_key = os.getenv("HISTORICAL_API_KEY")
        self.client_id = os.getenv("HISTORICAL_CLIENT_ID")
        self.password = os.getenv("HISTORICAL_PASSWORD")
        self.totp_token = os.getenv("HISTORICAL_TOTP_TOKEN")
        
        # Fallback to main credentials if historical not set
        if not self.api_key or self.api_key == "your_historical_api_key_here":
            print("[HISTORICAL] Falling back to main API credentials")
            self.api_key = os.getenv("ANGELONE_API_KEY")
            self.client_id = os.getenv("ANGELONE_CLIENT_ID")
            self.password = os.getenv("ANGELONE_PASSWORD")
            self.totp_token = os.getenv("ANGELONE_TOTP_TOKEN")
        
        self.smart_api = None
        self.is_logged_in = False
        self._initialized = True
        
        print("[HISTORICAL] Historical Data Service initialized")
    
    def login(self) -> bool:
        """
        Authenticate with Angel One using HISTORICAL credentials
        """
        if not all([self.api_key, self.client_id, self.password, self.totp_token]):
            print("[HISTORICAL] Missing credentials in .env")
            return False
        
        try:
            print("[HISTORICAL] Logging in to Angel One (Historical API)...")
            
            # Initialize SmartConnect
            self.smart_api = SmartConnect(api_key=self.api_key)
            
            # Generate TOTP
            totp = pyotp.TOTP(self.totp_token)
            totp_code = totp.now()
            
            # Login
            session = self.smart_api.generateSession(
                self.client_id,
                self.password,
                totp_code
            )
            
            if not session.get('status'):
                print(f"[HISTORICAL] [ERROR] Login failed: {session.get('message', 'Unknown error')}")
                return False
            
            self.is_logged_in = True
            print(f"[HISTORICAL] Login successful!")
            return True
            
        except Exception as e:
            print(f"[HISTORICAL] [ERROR] Login error: {e}")
            self.is_logged_in = False
            return False
    
    def get_historical_candles(
        self,
        ticker: str,
        interval: str,
        from_date: date,
        to_date: date,
        exchange: str = "NSE"
    ) -> List[Dict]:
        """
        Fetch historical candle data from Angel One
        
        Args:
            ticker: Stock ticker (e.g., 'RELIANCE', 'TCS')
            interval: 'ONE_DAY', 'ONE_HOUR', 'FIFTEEN_MINUTE', 'FIVE_MINUTE'
            from_date: Start date
            to_date: End date
            exchange: 'NSE' or 'BSE'
        
        Returns:
            List of candles: [
                {
                    'timestamp': datetime,
                    'open': float,
                    'high': float,
                    'low': float,
                    'close': float,
                    'volume': int
                },
                ...
            ]
        """
        if not self.is_logged_in:
            print("[HISTORICAL] [ERROR] Not logged in. Call login() first.")
            return []
        
        # Import angelone_service to use its token lookup
        from angelone_service import angelone_service
        
        # Get instrument token (reuse from main service)
        ticker_clean = ticker.replace('.NS', '').replace('.BO', '')
        token_data = angelone_service.get_token(ticker_clean, exchange)
        
        if not token_data:
            print(f"[HISTORICAL] [ERROR] Ticker '{ticker}' not found")
            return []
        
        try:
            # Prepare API params
            params = {
                "exchange": exchange,
                "symboltoken": token_data['token'],
                "interval": interval,
                "fromdate": from_date.strftime("%Y-%m-%d 09:15"),
                "todate": to_date.strftime("%Y-%m-%d 15:30")
            }
            
            # Call Angel One Historical API
            response = self.smart_api.getCandleData(params)
            
            if not response.get('status'):
                error_msg = response.get('message', 'Unknown error')
                print(f"[HISTORICAL] [ERROR] API error: {error_msg}")
                return []
            
            # Parse candles
            candles = []
            for candle in response.get('data', []):
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
            
        except Exception as e:
            print(f"[HISTORICAL] [ERROR] Error fetching candles: {e}")
            return []
    
    def logout(self):
        """Terminate historical API session"""
        if self.smart_api and self.is_logged_in:
            try:
                self.smart_api.terminateSession(self.client_id)
                print("[HISTORICAL] Logged out")
            except Exception as e:
                print(f"[HISTORICAL] Logout error: {e}")
            finally:
                self.is_logged_in = False


# Global singleton instance
historical_service = HistoricalDataService()


# ============================================================================
# Quick Test
# ============================================================================

if __name__ == "__main__":
    from datetime import date
    
    print("\n" + "=" * 70)
    print("TESTING HISTORICAL DATA SERVICE")
    print("=" * 70)
    
    # Login
    if historical_service.login():
        print("\n[OK] Login successful!")
        
        # Fetch 1 month of daily data for RELIANCE
        candles = historical_service.get_historical_candles(
            ticker='RELIANCE',
            interval='ONE_DAY',
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 31)
        )
        
        if candles:
            print(f"\n[OK] Fetched {len(candles)} candles")
            print("\nFirst 3 candles:")
            for candle in candles[:3]:
                print(f"  {candle['timestamp'].date()} | O:{candle['open']:.2f} "
                      f"H:{candle['high']:.2f} L:{candle['low']:.2f} "
                      f"C:{candle['close']:.2f} V:{candle['volume']:,}")
        else:
            print("\n[ERROR] No candles fetched")
        
        # Logout
        historical_service.logout()
    else:
        print("\n[ERROR] Login failed")
