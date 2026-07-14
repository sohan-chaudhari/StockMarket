from historical_service import historical_service
import datetime
historical_service.login()
c = historical_service.get_historical_candles('RELIANCE', 'THIRTY_MINUTE', datetime.datetime(2026,1,1), datetime.datetime(2026,1,10))
print('Fetched', len(c), 'candles')
