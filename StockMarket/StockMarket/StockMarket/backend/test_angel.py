from angelone_service import AngelOneService
import asyncio
async def test():
    service = AngelOneService()
    service.login()
    
    nifty = await service.get_live_price("NIFTY")
    sensex = await service.get_live_price("SENSEX")
    print(f"NIFTY: {nifty}")
    print(f"SENSEX: {sensex}")

asyncio.run(test())
