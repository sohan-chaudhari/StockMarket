import yfinance as yf
import pandas as pd
from backend import models, database
from datetime import date

db = database.SessionLocal()

target_ticker = "SENSEX.NS"

print(f"--- Debugging DB for {target_ticker} ---")
count = db.query(models.StockData).filter(models.StockData.ticker == target_ticker).count()
print(f"Record count for {target_ticker}: {count}")

if count > 0:
    last = db.query(models.StockData).filter(models.StockData.ticker == target_ticker).order_by(models.StockData.date.desc()).first()
    print(f"Last record: {last.date} - Close: {last.close}")

print("\n--- Testing yfinance logic ---")
yf_ticker = "^BSESN"
print(f"Downloading {yf_ticker} (1y)...")
df = yf.download(yf_ticker, period="1y", interval="1d", progress=False)
print(f"Downloaded shape: {df.shape}")

if not df.empty:
    print("Columns:", df.columns)
    print("First Row index type:", type(df.index[0]))
    
    first_row = df.iloc[0]
    print("First Row raw:", first_row)
    
    # Test Parsing Logic
    try:
        def get_val(row, col):
            val = row[col]
            if hasattr(val, 'iloc'): return float(val.iloc[0])
            return float(val)

        rec_open = get_val(first_row, 'Open')
        print(f"Parsed Open: {rec_open}")
    except Exception as e:
        print(f"Parsing Failed: {e}")

db.close()
