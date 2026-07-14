import asyncio
from main import get_live_prices_batch, schemas
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
try:
    response = client.post("/api/live-prices", json={"tickers": ["SENSEX"]})
    print(response.json())
except Exception as e:
    import traceback
    traceback.print_exc()
