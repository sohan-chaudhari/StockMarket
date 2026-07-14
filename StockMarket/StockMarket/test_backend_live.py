import requests
import time

# Test the live data endpoint
url = "http://127.0.0.1:8000/api/live-prices"
tickers = ["RELIANCE.NS"]

for i in range(3):
    print(f"\n--- Request {i+1} at {time.strftime('%H:%M:%S')} ---")
    try:
        response = requests.post(url, json={"tickers": tickers})
        if response.ok:
            data = response.json()
            print(f"Response: {data}")
        else:
            print(f"Error: {response.status_code}")
    except Exception as e:
        print(f"Exception: {e}")
    
    if i < 2:
        time.sleep(3)
