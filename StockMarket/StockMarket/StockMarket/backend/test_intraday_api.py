import requests
import pandas as pd
from datetime import datetime

BASE_URL = "http://localhost:8000"

def test_intraday_api(ticker, interval):
    print(f"Testing {ticker} with interval {interval}...")
    url = f"{BASE_URL}/api/stock-data/intraday"
    params = {"ticker": ticker, "interval": interval}
    
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if not data:
                print(f"No data returned for {ticker} {interval}")
                return
            
            print(f"Received {len(data)} candles.")
            print("First candle:", data[0])
            print("Last candle:", data[-1])
            
            # Check interval roughly
            if len(data) > 1:
                t1 = pd.to_datetime(data[0]['timestamp'])
                t2 = pd.to_datetime(data[1]['timestamp'])
                diff = t2 - t1
                print(f"Time difference between first two candles: {diff}")
        else:
            print(f"Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    # Test 5m (Raw)
    test_intraday_api("RELIANCE", "5m")
    
    # Test 15m (Resampled)
    test_intraday_api("RELIANCE", "15m")
    
    # Test 1h (Resampled)
    test_intraday_api("RELIANCE", "1h")
