import asyncio
import websockets
import json

async def test_ws():
    async with websockets.connect("ws://127.0.0.1:8003/ws/dashboard") as websocket:
        msg = await websocket.recv()
        data = json.loads(msg)
        print("First msg:", data.keys() if isinstance(data, dict) else data)
        if data.get("type") == "price_update":
            print(data.get("data", {}).get("NIFTY"))
            
        msg = await websocket.recv()
        data = json.loads(msg)
        print("Second msg:", data.keys() if isinstance(data, dict) else data)
        if data.get("type") == "price_update":
            print(data.get("data", {}).get("NIFTY"))

asyncio.run(test_ws())
