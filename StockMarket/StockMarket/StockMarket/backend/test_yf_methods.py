import yfinance as yf
import pandas as pd

ticker = "TCS.NS"
print(f"--- Fetching Data for {ticker} ---")

# Method 1: Download (Candles)
print("\n[Method 1: yf.download(period='1d')]")
try:
    df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=False)
    if not df.empty:
        day_open = float(df['Open'].iloc[0])
        day_high = float(df['High'].max())
        day_low = float(df['Low'].min())
        curr = float(df['Close'].iloc[-1])
        print(f"Candle Stats: Open={day_open}, High={day_high}, Low={day_low}, Current={curr}")
        print(f"First Candle Time: {df.index[0]}")
    else:
        print("Empty DataFrame")
except Exception as e:
    print(f"Download Error: {e}")

# Method 2: fast_info (Quote)
print("\n[Method 2: fast_info]")
try:
    dat = yf.Ticker(ticker)
    info = dat.fast_info
    print(f"FastInfo: LastPrice={info.last_price}")
    print(f"FastInfo: Open={info.open}")
    print(f"FastInfo: DayHigh={info.day_high}")
    print(f"FastInfo: DayLow={info.day_low}")
    print(f"FastInfo: PrevClose={info.previous_close}")
except Exception as e:
    print(f"FastInfo Error: {e}")
