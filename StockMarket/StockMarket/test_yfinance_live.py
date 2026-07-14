import yfinance as yf
from datetime import datetime
import time

ticker = "RELIANCE.NS"

for i in range(3):
    print(f"\n=== Attempt {i+1} at {datetime.now().strftime('%H:%M:%S')} ===")
    
    dat = yf.Ticker(ticker)
    hist = dat.history(period="1d", interval="1m")
    
    if not hist.empty:
        print(f"Total candles today: {len(hist)}")
        print(f"\nLast 3 candles:")
        print(hist.tail(3)[['Open', 'High', 'Low', 'Close', 'Volume']])
        
        last = hist.iloc[-1]
        print(f"\nLast candle close (current price): {last['Close']}")
        print(f"Last candle timestamp: {hist.index[-1]}")
    else:
        print("No data returned!")
    
    if i < 2:
        print("\nWaiting 5 seconds...")
        time.sleep(5)
