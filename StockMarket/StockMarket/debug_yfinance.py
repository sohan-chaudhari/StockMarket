
import yfinance as yf
import math
import sys

def safe_float(val):
    try:
        if val is None or math.isnan(val): return None
        return float(val)
    except:
        return None

def fetch_live_data_yfinance(ticker: str):
    print(f"\n--- Fetching {ticker} via yfinance ---")
    try:
        dat = yf.Ticker(ticker)
        
        # Method A: fast_info
        info = dat.fast_info
        print(f"Info keys: {info.keys()}")
        print(f"Last Price: {info.last_price}")
        print(f"Open: {info.open}")
        
        data = {}
        data['current_price'] = safe_float(info.last_price)
        data['open'] = safe_float(info.open)
        data['high'] = safe_float(info.day_high)
        data['low'] = safe_float(info.day_low)
        data['previous_close'] = safe_float(info.previous_close)
        
        print(f"Fast Info Data: {data}")
        
        # Fallback Logic Test
        if not data['current_price'] or not data['open']:
            print("fast_info incomplete, fetching 1d history...")
            hist = dat.history(period="1d")
            print(f"History (Last Row):\n{hist.iloc[-1] if not hist.empty else 'Empty'}")
            if not hist.empty:
                row = hist.iloc[-1]
                data['current_price'] = float(row['Close'])
                data['open'] = float(row['Open'])
                data['high'] = float(row['High'])
                data['low'] = float(row['Low'])
                data['volume'] = int(row['Volume'])
        else:
             hist = dat.history(period="1d")
             if not hist.empty:
                 data['volume'] = int(hist.iloc[-1]['Volume'])
                 print(f"Volume from history: {data['volume']}")
             else:
                 data['volume'] = 0

        print(f"Final Data: {data}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    fetch_live_data_yfinance("^NSEI") # NIFTY
    fetch_live_data_yfinance("RELIANCE.NS")
    fetch_live_data_yfinance("INFY.NS")
