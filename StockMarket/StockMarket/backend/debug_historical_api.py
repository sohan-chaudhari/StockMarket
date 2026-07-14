from datetime import datetime
from backend.historical_service import HistoricalDataService

def debug_api():
    print("Initializing Historical Service...")
    service = HistoricalDataService()
    
    if not service.login():
        print("Login failed.")
        return

    ticker = "RELIANCE"
    interval = "FIVE_MINUTE"
    
    # Test a very recent 1-day range (e.g., yesterday or 2 days ago)
    # Ensure it's a weekday ideally. February 12, 2025 was a Wednesday.
    from_date = datetime(2025, 2, 12, 9, 15)   # 9:15 AM
    to_date = datetime(2025, 2, 12, 15, 30)     # 3:30 PM
    
    print(f"Fetching {ticker} ({interval}) from {from_date} to {to_date}...")
    
    candles = service.get_historical_candles(
        ticker=ticker,
        interval=interval,
        from_date=from_date,
        to_date=to_date,
        exchange="NSE"
    )
    
    print(f"Result: {len(candles)} candles found.")
    if candles:
        print("First Candle:", candles[0])
        print("Last Candle:", candles[-1])
    else:
        print("No candles returned.")

if __name__ == "__main__":
    debug_api()
