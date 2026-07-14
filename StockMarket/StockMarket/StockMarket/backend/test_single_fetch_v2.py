import yfinance as yf
import pandas as pd
from typing import List, Dict

def fetch_batch_live_data(tickers: List[str]):
    print(f"Testing for tickers: {tickers}")
    try:
        data = yf.download(tickers, period="1d", interval="1m", progress=False, group_by="ticker", auto_adjust=True)
        
        for ticker in tickers:
            try:
                print(f"--- Processing {ticker} ---")
                try:
                    df = data[ticker]
                    print("Accessed data[ticker] successfully.")
                except KeyError:
                    print("KeyError on data[ticker].")
                    if len(tickers) == 1:
                        print("Fallback to df = data")
                        df = data
                    else:
                        print("Skipping.")
                        continue

                if isinstance(df, pd.Series):
                    # Sometimes data[ticker] might return Series if only 1 row/col? Unlikely for dataframe slice
                    print("Got Series instead of DataFrame")
                
                print("Columns:", df.columns)
                last_row = df.iloc[-1]
                print(f"Last Close: {last_row['Close']}")
                
            except Exception as e:
                print(f"Error parsing {ticker}: {e}")

fetch_batch_live_data(['TCS.NS'])
