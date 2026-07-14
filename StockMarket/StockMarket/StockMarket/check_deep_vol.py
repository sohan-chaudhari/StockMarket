
import yfinance as yf
import pandas as pd

def check_deep(ticker):
    print(f"\n[{ticker}] Checking 1mo history...")
    t = yf.Ticker(ticker)
    hist = t.history(period="1mo")
    if hist.empty:
        print("No history.")
        return
        
    vol_sum = hist['Volume'].sum()
    print(f"Total Volume (1mo): {vol_sum}")
    print("Sample rows with non-zero volume:")
    print(hist[hist['Volume'] > 0].head(3))

check_deep("^NSEI")
check_deep("^NSEBANK")
check_deep("^BSESN")
