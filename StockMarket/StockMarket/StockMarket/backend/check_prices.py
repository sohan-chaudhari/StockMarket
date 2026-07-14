import yfinance as yf

def check_price(ticker):
    print(f"--- Checking {ticker} ---")
    t = yf.Ticker(ticker)
    try:
        fast = t.fast_info
        print(f"FastInfo LastPrice: {fast.last_price}")
        print(f"FastInfo PrevClose: {fast.previous_close}")
        
        hist = t.history(period="5d", auto_adjust=False)
        print("History (Last 3 rows):")
        print(hist[['Close', 'Volume']].tail(3))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_price("TCS.NS")
    check_price("RELIANCE.NS")
    check_price("HDFCBANK.NS")
