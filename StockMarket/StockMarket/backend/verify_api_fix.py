import requests
import json

BASE_URL = "http://127.0.0.1:8003"

def test_intraday_api(ticker="RELIANCE", interval="15m"):
    print(f"Testing {ticker} ({interval})...")
    try:
        url = f"{BASE_URL}/api/stock-data/intraday"
        params = {"ticker": ticker, "interval": interval}
        response = requests.get(url, params=params)
        
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Success! Received {len(data)} candles.")
            if data:
                print(f"First candle timestamp: {data[0]['timestamp']}")
                print(f"Last candle timestamp: {data[-1]['timestamp']}")
                # Check for indicators in last candle
                last = data[-1]
                indicators = [k for k in last.keys() if k in ['sma_20', 'ema_20', 'rsi_14', 'macd']]
                print(f"Indicators found: {indicators}")
        else:
            print(f"Error Body: {response.text}")
            
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    # Test base interval
    test_intraday_api("RELIANCE", "15m")
    # Test resampling
    test_intraday_api("RELIANCE", "30m")
    # Test another interval
    test_intraday_api("RELIANCE", "1h")
