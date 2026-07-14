import asyncio
from angelone_service import AngelOneService

async def test_angel_sensex():
    service = AngelOneService()
    await service.ensure_connection()
    res = await service.get_live_price("SENSEX")
    print("AngelOne SENSEX Live Price:")
    print(res)

asyncio.run(test_angel_sensex())
