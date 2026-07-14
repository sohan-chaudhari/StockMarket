import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8000/ws/live/INFY.NS"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Waiting for messages...")
            while True:
                msg = await websocket.recv()
                print(f"Received: {msg}")
                # Just verify one message then exit
                break
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
