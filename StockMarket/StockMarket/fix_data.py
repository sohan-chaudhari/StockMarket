from backend.database import SessionLocal
from backend import models
import yfinance as yf
from datetime import date
import sys

def fix_data():
    db = SessionLocal()
    today = date.today()
    tickers = ["HDFCBANK.NS", "ALKEM.NS"]
    
    print(f"Fixing data for {today}")
    
    for ticker in tickers:
        candle = db.query(models.CurrentDayCandle).filter(
            models.CurrentDayCandle.ticker == ticker,
            models.CurrentDayCandle.trading_date == today
        ).first()
        
        if not candle:
            print(f"No candle for {ticker}")
            continue
            
        print(f"Processing {ticker}...")
        
        # Fix Open
        try:
             dat = yf.Ticker(ticker)
             hist = dat.history(period="1d")
             if not hist.empty:
                 yf_open = float(hist['Open'].iloc[0])
                 yf_close = float(hist['Close'].iloc[0])
                 
                 print(f"  DB Open: {candle.open} | YF Open: {yf_open}")
                 if abs(candle.open - yf_open) > 0.01:
                     print(f"  -> CORRECTING OPEN to {yf_open}")
                     candle.open = yf_open
                     
                 # Also ensure close is close to YF close?
                 # Google Finance might differ slightly, but let's check.
                 print(f"  DB Close: {candle.close} | YF Close: {yf_close}")
                 
        except Exception as e:
             print(f"Error fetching YF: {e}")

        db.commit()
    
    print("Done")

if __name__ == "__main__":
    # Ensure backend package is resolveable
    import os
    sys.path.append(os.getcwd())
    fix_data()
