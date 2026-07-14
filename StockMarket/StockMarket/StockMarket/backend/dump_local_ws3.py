import asyncio
import websockets
import json

async def test_ws():
    async with websockets.connect("ws://127.0.0.1:8003/ws/dashboard") as websocket:
        while True:
            msg = await websocket.recv()
            data = json.loads(msg)
            if data.get("type") == "price_update":
                nifty = data.get("data", {}).get("NIFTY", {})
                print("GOT PRICE UPDATE! NIFTY:", nifty)
                break

asyncio.run(test_ws())
