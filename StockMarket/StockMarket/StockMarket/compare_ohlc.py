import yfinance as yf
from datetime import datetime

ticker = "RELIANCE.NS"
print(f"Checking live data for {ticker} at {datetime.now()}")

dat = yf.Ticker(ticker)

# 1. Fast Info
try:
    info = dat.fast_info
    print("\n--- FAST INFO ---")
    print(f"Last Price: {info.last_price}")
    print(f"Open: {info.open}")
    print(f"Day High: {info.day_high}")
    print(f"Day Low: {info.day_low}")
    print(f"Prev Close: {info.previous_close}")
except Exception as e:
    print(f"Fast Info error: {e}")
    
# 2. History 1d 1m
try:
    print("\n--- HISTORY (1d, 1m) ---")
    hist = dat.history(period="1d", interval="1m")
    if not hist.empty:
        print(f"Total candles: {len(hist)}")
        print("\nFirst candle (Day Open):")
        print(hist.head(1))
        print("\nLast candle (Current):")
        print(hist.tail(1))
        print("\nDay Summary from 1m history:")
        print(f"Open: {hist['Open'].iloc[0]}")
        print(f"High: {hist['High'].max()}")
        print(f"Low: {hist['Low'].min()}")
        print(f"Close (Last): {hist['Close'].iloc[-1]}")
        print(f"Volume (Total): {hist['Volume'].sum()}")
    else:
        print("History (1d, 1m) is empty")
except Exception as e:
    print(f"History 1m error: {e}")

print("\n--- DATABASE VALUES ---")
print("Open: 1473.9000244140625")
print("High: 1489.5")
print("Low: 1435.0")
print("Current: 1450.800048828125")
