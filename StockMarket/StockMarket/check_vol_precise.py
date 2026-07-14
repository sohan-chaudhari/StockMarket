
import yfinance as yf

def get_vol(symbol):
    try:
        t = yf.Ticker(symbol)
        # Try history
        hist = t.history(period="5d")
        if not hist.empty:
            vol = hist.iloc[-1]['Volume']
            print(f"{symbol} Volume (History): {vol}")
        else:
            print(f"{symbol} History Empty")
            
        # Try fast_info
        # print(f"{symbol} FastInfo Vol: {t.fast_info.last_volume}")
    except Exception as e:
        print(f"{symbol} Error: {e}")

get_vol("^NSEI")
get_vol("^NSEBANK")
get_vol("^BSESN")
