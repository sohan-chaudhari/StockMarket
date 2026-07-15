import sys
sys.path.append("C:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend")
from database import SessionLocal
from main import perform_on_demand_backfill
from datetime import datetime, timedelta

def run_test():
    ticker = "STALLION"
    interval = "5m"
    now = datetime.now()
    last_stored_dt = now - timedelta(days=12) # ~ July 3
    print(f"Testing gap fill for {ticker} from {last_stored_dt} to {now}...")
    try:
        perform_on_demand_backfill(ticker, interval, last_stored_dt, now)
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_test()
