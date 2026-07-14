import requests
# Attempt to get some info if there's a status endpoint.
# There isn't one. I'll use a script that connects via WS and checks if it gets updates.

import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8000/ws/live/RELIANCE.NS"
    async with websockets.connect(uri) as websocket:
        print("Connected to WS")
        # Receive connection confirmation
        msg = await websocket.recv()
        print(f"Conf: {msg}")
        
        # Receive first update (triggered by process_ticker)
        msg = await websocket.recv()
        print(f"First Update: {msg}")
        
        # Wait for next update from loop (should be within 3-5s)
        try:
            msg = await asyncio.wait_for(websocket.recv(), timeout=10)
            print(f"Loop Update: {msg}")
        except asyncio.TimeoutError:
            print("Timeout! Loop might not be running correctly.")

asyncio.run(test_ws())
