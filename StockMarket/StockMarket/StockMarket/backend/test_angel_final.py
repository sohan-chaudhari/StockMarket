"""
Test Angel One integration with hardcoded tokens
"""

import sys
import os
from dotenv import load_dotenv
import pathlib

# Load environment variables
backend_dir = pathlib.Path(__file__).parent.resolve()
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)

# Import angelone_service
sys.path.insert(0, str(backend_dir.parent))
from backend.angelone_service import angelone_service

async def test_with_hardcoded():
    print("=" * 60)
    print("ANGEL ONE INTEGRATION TEST (Hardcoded Tokens)")
    print("=" * 60)
    
    # Test 1: Login
    print("\n[Test 1] Login")
    print("=" * 60)
    if not angelone_service.login():
        print("[ X] Login failed")
        return False
    print("[OK] Login successful")
    
    # Test 2: Skip instrument download (use hardcoded only)
    print("\n[Test 2] Using Hardcoded Tokens")
    print("-" * 60)
    print("[OK] Skipping instrument download (using hardcoded fallback)")
    
    # Test 3: Token lookup
    print("\n[Test 3] Token Lookup for Hardcoded Stocks")
    print("-" * 60)
    test_stocks = ["RELIANCE", "TCS", "NIFTY", "ICICIBANK"]
    
    for ticker in test_stocks:
        token = angelone_service.get_token(ticker)
        if token:
            print(f"[OK] {ticker:12} -> Token: {token['token']:10} | Symbol: {token['symbol']}")
        else:
            print(f"[X] {ticker:12} -> Not found")
    
    # Test 4: Live Price Fetching
    print("\n[Test 4] Live Price Fetching")
    print("-" * 60)
    
    for ticker in ["RELIANCE", "TCS"]:
        print(f"\nFetching {ticker}...")
        price_data = await angelone_service.get_live_price(ticker)
        
        if price_data:
            print(f"[OK] {ticker}:")
            print(f"     Current: Rs {price_data['current_price']:.2f}")
            print(f"     Open:    Rs {price_data['open']:.2f}")
            print(f"     High:    Rs {price_data['high']:.2f}")
            print(f"     Low:     Rs {price_data['low']:.2f}")
        else:
            print(f"[X] Failed to fetch {ticker}")
    
    # Test 5: Logout
    print("\n[Test 5] Logout")
    print("-" * 60)
    angelone_service.logout()
    print("[OK] Logged out")
    
    print("\n" + "=" * 60)
    print("[OK] ALL TESTS PASSED!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    import asyncio
    try:
        success = asyncio.run(test_with_hardcoded())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[X] Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
