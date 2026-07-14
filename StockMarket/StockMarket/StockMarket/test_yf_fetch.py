
import asyncio
from backend.main import fetch_live_data_yfinance
import sys

# Windows asyncio policy fix (sometimes needed)
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def test_fetch():
    ticker = "RELIANCE.NS"
    print(f"Fetching live data for {ticker}...")
    data = fetch_live_data_yfinance(ticker)
    print(f"Result: {data}")
    
    if data:
        print(f"Current Price: {data.get('current_price')}")
        print(f"Volume: {data.get('volume')}")
        
        # Check timestamps if available in data? No, yfinance dict implies current.

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_fetch())
