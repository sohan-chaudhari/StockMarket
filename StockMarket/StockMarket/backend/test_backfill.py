import sys
sys.path.append('c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend')
from main import perform_on_demand_backfill, database, datetime, timedelta
now = database.get_ist_now()
backfill_start = now - timedelta(days=5)
perform_on_demand_backfill('INFY', '15m', backfill_start, now)
