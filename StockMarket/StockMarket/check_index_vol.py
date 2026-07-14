
import yfinance as yf
import sys

def check_vol(ticker):
    print(f"\n--- CHECKING {ticker} ---")
    dat = yf.Ticker(ticker)
    hist = dat.history(period="5d")
    if hist.empty:
        print("History is EMPTY.")
    else:
        last_row = hist.iloc[-1]
        print(f"Date: {last_row.name}")
        print(f"Close: {last_row['Close']}")
        print(f"Volume: {last_row['Volume']}")

tickers = ["^NSEI", "^NSEBANK", "^BSESN"]
for t in tickers:
    check_vol(t)
    sys.stdout.flush()
