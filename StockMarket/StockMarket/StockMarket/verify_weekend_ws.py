
import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8001/ws/live/RELIANCE.NS"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            # First message might be "connected"
            msg1 = await websocket.recv()
            print(f"Msg 1: {msg1}")
            
            # Second message should be the candle update
            msg2 = await websocket.recv()
            print(f"Msg 2: {msg2}")
            
            data = json.loads(msg2)
            if data.get('type') == 'candle_update':
                c = data['data']
                print(f"\n--- VERIFICATION ---")
                print(f"Ticker: {c['ticker']}")
                print(f"Price: {c['current_price']}")
                print(f"Finalized: {c['is_finalized']}")
                if c['is_finalized']:
                    print("SUCCESS: Weekend/Market Closed logic active.")
                else:
                    print("FAILURE: Data not finalized.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
