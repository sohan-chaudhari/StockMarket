import yfinance as yf
import pandas as pd
import os

def fetch_1h_data_nov_dec_2025():
    # List of some prominent tickers (Nifty 50 style)
    tickers = ['RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS', 'ICICIBANK.NS']
    
    start_date = "2025-11-01"
    end_date = "2025-12-31"
    
    print(f"Fetching 1-hour data from {start_date} to {end_date}...")
    
    # yfinance allows 1h data up to 730 days (2 years), so 2025 is well within range
    data = yf.download(tickers, start=start_date, end=end_date, interval="1h", group_by='ticker')
    
    if not os.path.exists('historical_data'):
        os.makedirs('historical_data')
        
    output_file = 'historical_data/nov_dec_2025_1h.csv'
    data.to_csv(output_file)
    print(f"Successfully downloaded 1h data for Nov-Dec 2025 and saved to {output_file}")

if __name__ == "__main__":
    fetch_1h_data_nov_dec_2025()
