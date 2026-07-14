
import requests

def refresh(ticker):
    print(f"Refreshing {ticker}...")
    try:
        r = requests.post("http://localhost:8000/api/fetch-stock", json={"ticker": ticker})
        print(r.json())
    except Exception as e:
        print(f"Error: {e}")

refresh("BANKNIFTY")
refresh("SENSEX")
