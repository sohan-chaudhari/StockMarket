import yfinance as yf
t = yf.Ticker("HDFCBANK.NS")
print(f"HDFCBANK Last Volume: {t.fast_info.last_volume}")
