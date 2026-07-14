import asyncio
import websockets
import json

async def test_ws():
    async with websockets.connect("ws://127.0.0.1:8003/ws/dashboard") as websocket:
        msg = await websocket.recv()
        data = json.loads(msg)
        print("First msg:")
        if data.get("type") == "price_update":
            nifty = data.get("data", {}).get("NIFTY", {})
            print(f"NIFTY: {nifty}")
            
        # receive one more update
        msg = await websocket.recv()
        data = json.loads(msg)
        print("Second msg:")
        if data.get("type") == "price_update":
            nifty = data.get("data", {}).get("NIFTY", {})
            print(f"NIFTY: {nifty}")

asyncio.run(test_ws())
