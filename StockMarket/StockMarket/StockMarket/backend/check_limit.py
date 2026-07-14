from backend.historical_service import HistoricalDataService
from datetime import datetime
import pandas as pd

def check_history():
    print("Logging in...")
    service = HistoricalDataService()
    if not service.login():
        print("Login failed")
        return

    ticker = "RELIANCE"
    # Try fetching data for early 2023
    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 1, 10) # process just 10 days
    
    intervals = ["FIVE_MINUTE", "TEN_MINUTE", "FIFTEEN_MINUTE", "ONE_HOUR", "ONE_DAY"]
    
    print(f"\nChecking data availability for {ticker} from {start_date.date()} to {end_date.date()}...\n")
    
    for interval in intervals:
        print(f"--- Interval: {interval} ---")
        candles = service.get_historical_candles(ticker, interval, start_date, end_date)
        if candles:
            print(f"SUCCESS: Found {len(candles)} candles.")
            print(f"First candle: {candles[0]}")
            print(f"Last candle: {candles[-1]}")
        else:
            print("FAILURE: No data returned.")
        print("")

if __name__ == "__main__":
    check_history()
