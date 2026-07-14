import yfinance as yf
import json
from datetime import datetime

def check_reliance():
    ticker = "RELIANCE.NS"
    print(f"Checking data for {ticker} at {datetime.now()}")
    
    dat = yf.Ticker(ticker)
    
    # 1. Fast Info
    try:
        info = dat.fast_info
        print("\n--- FAST INFO ---")
        print(f"Last Price: {info.last_price}")
        print(f"Open: {info.open}")
        print(f"High: {info.day_high}")
        print(f"Low: {info.day_low}")
        print(f"Prev Close: {info.previous_close}")
    except Exception as e:
        print(f"Fast Info error: {e}")
        
    # 2. History 1d 1m
    try:
        print("\n--- HISTORY (1d, 1m) last row ---")
        hist = dat.history(period="1d", interval="1m")
        if not hist.empty:
            print(hist.tail(1))
            print("\nDay Summary from 1m history:")
            print(f"Open: {hist['Open'].iloc[0]}")
            print(f"High: {hist['High'].max()}")
            print(f"Low: {hist['Low'].min()}")
            print(f"Close (Last): {hist['Close'].iloc[-1]}")
        else:
            print("History (1d, 1m) is empty")
    except Exception as e:
        print(f"History 1m error: {e}")

    # 3. Regular Info (Slow)
    try:
        print("\n--- REGULAR INFO ---")
        info_reg = dat.info
        print(f"Regular Open: {info_reg.get('open')}")
        print(f"Regular High: {info_reg.get('dayHigh')}")
        print(f"Regular Low: {info_reg.get('dayLow')}")
        print(f"Regular Current: {info_reg.get('currentPrice')}")
    except Exception as e:
        print(f"Regular info error: {e}")

if __name__ == "__main__":
    check_reliance()
