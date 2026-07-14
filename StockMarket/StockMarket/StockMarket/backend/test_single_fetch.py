import yfinance as yf
import pandas as pd
from typing import List, Dict
import datetime

def fetch_batch_live_data(tickers: List[str]) -> Dict[str, Dict]:
    print(f"Testing for tickers: {tickers}")
    try:
        data = yf.download(tickers, period="1d", interval="1m", progress=False, group_by="ticker", auto_adjust=True)
        
        results = {}
        is_multi = len(tickers) > 1
        
        print("\nType of data:", type(data))
        if isinstance(data, pd.DataFrame):
            print("Columns:", data.columns)
            if not data.empty:
                print("Last Row:\n", data.iloc[-1])
        
        for ticker in tickers:
            try:
                if is_multi:
                    df = data[ticker]
                else:
                    df = data
                
                if df.empty:
                    print(f"{ticker} DF Empty")
                    continue
                
                last_row = df.iloc[-1]
                print(f"{ticker} Last Close: {last_row['Close']}")
                
                results[ticker] = {'current_price': float(last_row['Close'])}
            except Exception as e:
                print(f"Error parsing {ticker}: {e}")
                
        return results
    except Exception as e:
        print(f"Global Error: {e}")
        return {}

# Run Test
res = fetch_batch_live_data(['TCS.NS'])
print("\nResult:", res)
