import yfinance as yf
from datetime import datetime

def resolve_yf_ticker(ticker: str) -> str:
    INDEX_MAP = {
        "NIFTY": {"yahoo": "^NSEI"},
        "BANKNIFTY": {"yahoo": "^NSEBANK"},
        "SENSEX": {"yahoo": "^BSESN"}
    }
    if ticker in INDEX_MAP:
        return INDEX_MAP[ticker]["yahoo"]
    if "." not in ticker and "^" not in ticker:
        return f"{ticker}.NS"
    return ticker

def test_fetch(ticker):
    yf_ticker = resolve_yf_ticker(ticker)
    print(f"\n--- Testing {ticker} -> {yf_ticker} ---")
    try:
        dat = yf.Ticker(yf_ticker)
        # Fetch generic 20d history
        df = dat.history(period="20d")
        
        if df.empty:
            print(f"❌ RESULT: Empty DataFrame returned for {yf_ticker}")
        else:
            print(f"✅ RESULT: Fetched {len(df)} rows.")
            print("Last 3 rows:")
            print(df.tail(3)[['Open', 'High', 'Low', 'Close', 'Volume']])
            
            # Check fast_info
            try:
                info = dat.fast_info
                print(f"✅ FastInfo LastPrice: {info.last_price}")
            except Exception as e:
                 print(f"❌ FastInfo Failed: {e}")

    except Exception as e:
        print(f"❌ EXCEPTION: {e}")

if __name__ == "__main__":
    print("Starting yfinance Debug...")
    test_fetch("NIFTY")
    test_fetch("RELIANCE")
    test_fetch("BANKNIFTY")
