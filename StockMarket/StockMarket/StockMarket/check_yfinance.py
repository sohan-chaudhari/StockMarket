import yfinance as yf
import datetime

def check_open():
    ticker = "HDFCBANK.NS"
    print(f"Fetching {ticker} from yfinance...")
    
    # Method 1: Ticker.info
    # Note: info can be slow or unreliable for real-time
    try:
        dat = yf.Ticker(ticker)
        # fast check history
        hist = dat.history(period="1d")
        if not hist.empty:
            open_price = hist['Open'].iloc[0]
            print(f"History (1d) Open: {open_price}")
        else:
            print("History empty")
            
        # info check
        # info = dat.info
        # print(f"Info Open: {info.get('open')}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_open()
