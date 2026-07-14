import yfinance as yf
from datetime import datetime

def check_open():
    ticker = "RELIANCE.NS"
    dat = yf.Ticker(ticker)
    hist = dat.history(period="1d", interval="1m")
    if not hist.empty:
        print("First 10 minutes of trading:")
        print(hist.head(10))
    else:
        print("No history found")

if __name__ == "__main__":
    check_open()
