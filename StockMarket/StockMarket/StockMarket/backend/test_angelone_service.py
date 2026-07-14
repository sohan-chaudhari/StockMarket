"""
Test script for Angel One service integration
Tests login, instrument loading, and live price fetching
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.angelone_service import angelone_service
import time

async def test_angelone_service():
    """Test Angel One service functionality"""
    
    print("=" * 60)
    print("ANGEL ONE SERVICE TEST")
    print("=" * 60)
    
    # Test 1: Login
    print("\n[Test 1] Login")
    print("-" * 60)
    login_success = angelone_service.login()
    
    if not login_success:
        print("[X] Login failed. Please check your credentials in .env file")
        return False
    
    print("[OK] Login successful")
    
    # Test 2: Load Instruments
    print("\n[Test 2] Load Instruments")
    print("-" * 60)
    load_success = angelone_service.load_instruments()
    
    if not load_success:
        print("[X] Failed to load instruments")
        return False
    
    instrument_count = len(angelone_service.instruments_df)
    print(f"[OK] Loaded {instrument_count} instruments")
    
    # Test 3: Token Lookup for Known Stocks
    print("\n[Test 3] Token Lookup")
    print("-" * 60)
    test_stocks = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "NIFTY"]
    
    for ticker in test_stocks:
        instrument = angelone_service.get_token(ticker)
        if instrument:
            print(f"[OK] {ticker:12} → Token: {instrument['token']:7} | Symbol: {instrument['symbol']}")
        else:
            print(f"[X] {ticker:12} → Not found")
    
    # Test 4: Live Price Fetching
    print("\n[Test 4] Live Price Fetching")
    print("-" * 60)
    
    for ticker in ["RELIANCE", "TCS", "NIFTY"]:
        print(f"\nFetching {ticker}...")
        price_data = await angelone_service.get_live_price(ticker)
        
        if price_data:
            print(f"[OK] {ticker}:")
            print(f"   Current Price: ₹{price_data['current_price']:.2f}")
            print(f"   Open:  ₹{price_data['open']:.2f}")
            print(f"   High:  ₹{price_data['high']:.2f}")
            print(f"   Low:   ₹{price_data['low']:.2f}")
            print(f"   Volume: {price_data['volume']:,}")
        else:
            print(f"[X] Failed to fetch price for {ticker}")
        
        await asyncio.sleep(1)  # Rate limiting
    
    # Test 5: Logout
    print("\n[Test 5] Logout")
    print("-" * 60)
    angelone_service.logout()
    print("[OK] Logged out successfully")
    
    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETED")
    print("=" * 60)
    return True

if __name__ == "__main__":
    import asyncio
    # Load environment variables from backend/.env
    from dotenv import load_dotenv
    import pathlib
    
    # Get the backend directory path
    backend_dir = pathlib.Path(__file__).parent.resolve()
    env_path = backend_dir / ".env"
    
    print(f"Loading environment from: {env_path}")
    load_dotenv(dotenv_path=env_path)
    
    # Verify credentials are loaded
    import os
    if not os.getenv("ANGELONE_API_KEY"):
        print("[X] ERROR: ANGELONE_API_KEY not found in .env file")
        print(f"   Please check that {env_path} exists and has your credentials")
        sys.exit(1)
    
    try:
        success = asyncio.run(test_angelone_service())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[X] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
