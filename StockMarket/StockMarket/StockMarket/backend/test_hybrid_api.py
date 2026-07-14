import requests
from datetime import datetime

BASE_URL = "http://127.0.0.1:8000"

def test_hybrid_api():
    ticker = "RELIANCE"
    
    print(f"Testing Hybrid API for {ticker}...\n")
    
    # Test 1: 5-minute data (Should be limited ~75 days)
    print("--- Test 1: 5-minute Data (Recent) ---")
    try:
        resp = requests.get(f"{BASE_URL}/api/stock-data/intraday", params={"ticker": ticker, "interval": "5m"})
        if resp.status_code == 200:
            data = resp.json()
            if data:
                print(f"Success! Retrieved {len(data)} records.")
                print(f"Start: {data[0]['timestamp']}")
                print(f"End:   {data[-1]['timestamp']}")
            else:
                print("No data returned for 5m.")
        else:
            print(f"Failed: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Error: {e}")
    print("")

    # Test 2: 15-minute data (Should range back to ~2023)
    print("--- Test 2: 15-minute Data (Extended History) ---")
    try:
        resp = requests.get(f"{BASE_URL}/api/stock-data/intraday", params={"ticker": ticker, "interval": "15m"})
        if resp.status_code == 200:
            data = resp.json()
            if data:
                print(f"Success! Retrieved {len(data)} records.")
                print(f"Start: {data[0]['timestamp']}")
                print(f"End:   {data[-1]['timestamp']}")
                
                # Verify start date is early 2023
                first_ts = data[0]['timestamp']
                if "2023-" in first_ts:
                    print("VERIFICATION PASSED: Data starts from 2023!")
                else:
                    print(f"VERIFICATION FAILED: Data starts from {first_ts}")
            else:
                print("No data returned for 15m.")
        else:
            print(f"Failed: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Error: {e}")
    print("")

if __name__ == "__main__":
    test_hybrid_api()
