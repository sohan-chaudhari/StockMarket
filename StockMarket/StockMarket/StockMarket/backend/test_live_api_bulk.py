import asyncio
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
tickers = ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "LT.NS", "BAJFINANCE.NS"]
try:
    response = client.post("/api/live-prices", json={"tickers": tickers})
    print(response.status_code)
    if response.status_code == 500:
        print(response.text)
except Exception as e:
    import traceback
    traceback.print_exc()
