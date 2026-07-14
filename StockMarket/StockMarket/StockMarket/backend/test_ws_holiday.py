import asyncio
import websockets
import json

async def test():
    uri = "ws://localhost:8000/ws/live/NIFTY"
    async with websockets.connect(uri) as websocket:
        print("Connected.")
        
        # Wait for messages
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=5)
                data = json.loads(msg)
                print(f"Received: {data}")
                
                # Check is_finalized
                if data.get('type') == 'candle_update':
                    finalized = data['data'].get('is_finalized')
                    print(f"Is Finalized: {finalized}")
                    break
            except asyncio.TimeoutError:
                print("Timeout waiting for message.")
                break

if __name__ == "__main__":
    asyncio.run(test())
