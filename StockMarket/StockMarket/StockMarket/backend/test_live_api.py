import asyncio
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
try:
    response = client.post("/api/live-prices", json={"tickers": ["NIFTY", "SENSEX", "RELIANCE"]})
    print(response.status_code)
    print(response.text)
except Exception as e:
    import traceback
    traceback.print_exc()
