import requests
import json
from datetime import date

base_url = "http://localhost:8000/api"
ticker = "HDFCBANK.NS"

def verify():
    # 1. Fetch History (Force Refresh)
    print(f"--> Triggering Force Fetch for {ticker}...")
    try:
        resp = requests.post(f"{base_url}/fetch-stock", json={"ticker": ticker})
        print(f"Fetch Status: {resp.status_code}")
        print(f"Response: {resp.json()}")
    except Exception as e:
        print(f"Fetch failed: {e}")
        return

    # 2. Get Hybrid Range Data
    print(f"\n--> Getting Range Data (1W)...")
    try:
        resp = requests.get(f"{base_url}/stock-data/range", params={"ticker": ticker, "range": "1W"})
        data = resp.json()
        print(f"Got {len(data)} records.")
        
        if not data:
            print("No data returned!")
            return

        # Print last 3 records
        print("\nLast 3 records:")
        for d in data[-3:]:
            print(d)
            
        # Check integrity
        last = data[-1]
        today_str = date.today().isoformat()
        
        if last['date'] == today_str:
            print("\n[SUCCESS] Last record date matches Today!")
            if last['volume'] == 0:
                 print("[SUCCESS] Volume is 0, indicating it's the Live Candle.")
            else:
                 print(f"[WARNING] Volume is {last['volume']}, might be from history?")
        else:
            print(f"\n[FAILURE] Last record date ({last['date']}) is NOT Today ({today_str})")
            
    except Exception as e:
        print(f"Get Range failed: {e}")

if __name__ == "__main__":
    verify()
