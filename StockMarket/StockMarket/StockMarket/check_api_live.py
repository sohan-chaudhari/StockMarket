import requests
import json

tickers = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'TCS', 'HDFCBANK']
url = "http://127.0.0.1:8000/api/live-prices"

try:
    response = requests.post(url, json={"tickers": tickers})
    if response.status_code == 200:
        data = response.json()
        print(json.dumps(data, indent=2))
    else:
        print(f"Error: {response.status_code} - {response.text}")
except Exception as e:
    print(f"Error: {e}")
