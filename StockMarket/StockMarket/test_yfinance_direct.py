"""Test yfinance directly to see if it returns data"""
import yfinance as yf
from datetime import datetime

ticker = "INFY.NS"
print(f"Testing yfinance for {ticker}...")
print("=" * 60)

stock = yf.Ticker(ticker)
df = stock.history(start="2015-01-01", end=datetime.now().strftime("%Y-%m-%d"), auto_adjust=True)

print(f"DataFrame shape: {df.shape}")
print(f"Date range: {df.index.min()} to {df.index.max()}")
print(f"Total rows: {len(df)}")
print("\nFirst 3 rows:")
print(df.head(3))
print("\nLast 3 rows:")
print(df.tail(3))

if len(df) > 0:
    print("\n✓ yfinance is working correctly")
else:
    print("\n✗ yfinance returned no data!")
