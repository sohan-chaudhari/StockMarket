import pandas as pd
from datetime import date
from historical_service import historical_service
import sys

def main(ticker="RELIANCE"):
    print(f"Fetching 1-hour candles for {ticker} (Nov - Dec 2025)...")
    if historical_service.login():
        candles = historical_service.get_historical_candles(
            ticker=ticker,
            interval='ONE_HOUR',
            from_date=date(2025, 11, 1),
            to_date=date(2025, 12, 31)
        )
        
        if candles:
            df = pd.DataFrame(candles)
            df.set_index('timestamp', inplace=True)
            print(f"\nSuccessfully fetched {len(df)} candles.")
            print("\n--- DataFrame Head ---")
            print(df.head())
            print("\n--- DataFrame Tail ---")
            print(df.tail())
        else:
            print(f"\nNo candles returned for {ticker}. The market might have been closed, or the ticker is invalid.")
            
        historical_service.logout()
    else:
        print("\nLogin failed. Check your HISTORICAL credentials in .env.")

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    main(ticker)
