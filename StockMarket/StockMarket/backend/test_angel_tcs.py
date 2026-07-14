from historical_service import historical_service
import datetime
historical_service.login()
candles = historical_service.get_historical_candles('TCS', 'FIVE_MINUTE', datetime.datetime(2026,7,4,9,15), datetime.datetime(2026,7,4,15,30))
print(f'Fetched {len(candles)} candles for today')
if len(candles)>0: print(candles[-1])
