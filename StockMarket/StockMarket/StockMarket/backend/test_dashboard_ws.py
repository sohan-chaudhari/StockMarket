import asyncio
import websockets
import json

async def test_ws():
    try:
        async with websockets.connect("ws://127.0.0.1:8003/ws/dashboard") as ws:
            print("Connected to dashboard WS!")
            # Wait for 3 messages
            for _ in range(3):
                msg = await ws.recv()
                print("Received:", msg)
    except Exception as e:
        print("WS Error:", e)

asyncio.run(test_ws())
