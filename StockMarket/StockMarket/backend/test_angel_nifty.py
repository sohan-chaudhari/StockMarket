from historical_service import historical_service
import datetime
historical_service.login()
token_data = historical_service.smart_api.getCandleData({'exchange': 'NSE', 'symboltoken': '26000', 'interval': 'FIVE_MINUTE', 'fromdate': '2026-07-01 09:15', 'todate': '2026-07-04 15:30'})
print(token_data)
