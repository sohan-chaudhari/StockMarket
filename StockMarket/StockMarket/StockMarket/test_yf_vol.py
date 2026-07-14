import yfinance as yf
ticker = yf.Ticker("RELIANCE.NS")
info = ticker.fast_info
print(f"Last Price: {info.last_price}")
try:
    print(f"Last Volume: {info.last_volume}")
except Exception as e:
    print(f"Error getting last_volume: {e}")

hist = ticker.history(period="1d")
if not hist.empty:
    print(f"History Volume: {hist.iloc[-1]['Volume']}")
else:
    print("History empty")
