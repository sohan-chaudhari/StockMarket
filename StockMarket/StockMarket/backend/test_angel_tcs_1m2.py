from historical_service import historical_service
import datetime
historical_service.login()
candles = historical_service.get_historical_candles('TCS', 'ONE_MINUTE', datetime.datetime(2026,7,4,9,15), datetime.datetime(2026,7,4,15,30))
for c in candles: print(c['timestamp'].strftime('%H:%M'))
