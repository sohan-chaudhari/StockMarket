import asyncio
from angelone_service import AngelOneService

async def test_angel_ws_sensex():
    service = AngelOneService()
    await service.ensure_connection()
    service.subscribe_tickers(["SENSEX"])
    
    # Wait for ticks
    for _ in range(10):
        await asyncio.sleep(1)
        tick = service.latest_ticks.get("SENSEX")
        if tick:
            print("WS Tick for SENSEX:", tick)
            break
    else:
        print("No WS tick received for SENSEX")

asyncio.run(test_angel_ws_sensex())
