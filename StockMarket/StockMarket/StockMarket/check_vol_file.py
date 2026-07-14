
import yfinance as yf
import pandas as pd

with open("vol_check_output.txt", "w") as f:
    for ticker in ["^NSEI", "^NSEBANK", "^BSESN"]:
        f.write(f"\n--- {ticker} ---\n")
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            vol_sum = hist['Volume'].sum()
            f.write(f"Total Volume (5d): {vol_sum}\n")
            f.write(f"Last Row:\n{hist.iloc[-1] if not hist.empty else 'Empty'}\n")
        except Exception as e:
            f.write(f"Error: {e}\n")
