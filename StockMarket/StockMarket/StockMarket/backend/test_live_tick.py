from angelone_service import AngelOneService
import asyncio

async def test():
    service = AngelOneService()
    service.login()
    service.subscribe_tickers(["NIFTY", "SENSEX"])
    
    await asyncio.sleep(2)
    print("NIFTY tick:", service.latest_ticks.get("NIFTY"))
    print("SENSEX tick:", service.latest_ticks.get("SENSEX"))

asyncio.run(test())
