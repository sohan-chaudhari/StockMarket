
import requests
import asyncio
import websockets
import json
from datetime import date

BASE_URL = "http://localhost:8001/api"
WS_URL = "ws://localhost:8001/ws/live"
TICKER = "RELIANCE.NS"

def test_flow():
    print(f"--- 1. Fetch Stock {TICKER} ---")
    try:
        r = requests.post(f"{BASE_URL}/fetch-stock", json={"ticker": TICKER})
        print(f"Status: {r.status_code}")
        print(f"Response: {r.json()}")
    except Exception as e:
        print(f"Fetch Failed: {e}")
        return

    print(f"\n--- 2. Get Range Data ---")
    try:
        r = requests.get(f"{BASE_URL}/stock-data/range?ticker={TICKER}&range=1M")
        data = r.json()
        print(f"Data Length: {len(data)}")
        if data:
            print(f"Last Candle: {data[-1]}")
    except Exception as e:
        print(f"Get Range Failed: {e}")

async def test_ws():
    print(f"\n--- 3. WebSocket Check ---")
    uri = f"{WS_URL}/{TICKER}"
    try:
        async with websockets.connect(uri) as ws:
            msg1 = await ws.recv()
            print(f"Msg 1: {msg1}")
            
            # Wait for data
            try:
                msg2 = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"Msg 2: {msg2}")
            except asyncio.TimeoutError:
                print("WS Timeout - No data received!")
    except Exception as e:
        print(f"WS Failed: {e}")

if __name__ == "__main__":
    test_flow()
    asyncio.run(test_ws())
