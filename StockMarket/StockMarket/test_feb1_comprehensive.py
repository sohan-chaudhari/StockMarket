"""
Try fetching Feb 1st with yfinance using different parameters
Maybe it's available with different settings
"""
import yfinance as yf
from datetime import date, datetime

def try_fetch_feb1(ticker_symbol, yf_ticker):
    """Try multiple methods to fetch Feb 1st data"""
    
    print(f"\n{'='*60}")
    print(f"Testing {ticker_symbol} ({yf_ticker})")
    print(f"{'='*60}")
    
    stock = yf.Ticker(yf_ticker)
    
    # Method 1: history() with specific date range
    print("\n1. Using history() for Feb 1st only:")
    try:
        df1 = stock.history(start="2026-02-01", end="2026-02-02", auto_adjust=False)
        if not df1.empty:
            print(f"   ✓ Found {len(df1)} records")
            for idx, row in df1.iterrows():
                print(f"   Date: {idx.date()}")
                print(f"   Open: {row['Open']}, High: {row['High']}, Low: {row['Low']}, Close: {row['Close']}")
        else:
            print("   ✗ No data")
    except Exception as e:
        print(f"   ✗ Error: {e}")
    
    # Method 2: history() for last 7 days
    print("\n2. Using history() for last 7 days:")
    try:
        df2 = stock.history(period="7d", auto_adjust=False)
        if not df2.empty:
            print(f"   ✓ Found {len(df2)} records")
            for idx, row in df2.iterrows():
                date_str = idx.date()
                print(f"   {date_str}: Close={row['Close']}")
        else:
            print("   ✗ No data")
    except Exception as e:
        print(f"   ✗ Error: {e}")
    
    # Method 3: download() instead of history()
    print("\n3. Using download() for Jan 28 - Feb 2:")
    try:
        df3 = yf.download(yf_ticker, start="2026-01-28", end="2026-02-03", progress=False, auto_adjust=False)
        if not df3.empty:
            print(f"   ✓ Found {len(df3)} records")
            for idx, row in df3.iterrows():
                date_str = idx.date()
                close_val = row['Close'].iloc[0] if hasattr(row['Close'], 'iloc') else row['Close']
                print(f"   {date_str}: Close={close_val}")
        else:
            print("   ✗ No data")
    except Exception as e:
        print(f"   ✗ Error: {e}")
    
    # Method 4: Check fast_info for last update
    print("\n4. Checking fast_info:")
    try:
        info = stock.fast_info
        print(f"   Last Price: {info.get('last_price', 'N/A')}")
        print(f"   Previous Close: {info.get('previous_close', 'N/A')}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

# Test with RELIANCE
print("Testing multiple methods to fetch Feb 1st, 2026 data")
print("If Feb 1st was a special Budget Day session, it should appear")

try_fetch_feb1("RELIANCE", "RELIANCE.NS")
try_fetch_feb1("NIFTY", "^NSEI")

print("\n" + "="*60)
print("CONCLUSION:")
print("="*60)
print("If all methods return 'No data' for Feb 1st, it confirms:")
print("  → Feb 1st was NOT a trading day (Sunday)")
print("  → TradingView might be showing interpolated/estimated data")
print("  → OR TradingView has data from a different source")
