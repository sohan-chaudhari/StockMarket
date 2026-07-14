import yfinance as yf

def test():
    ticker = "RELIANCE.NS"
    dat = yf.Ticker(ticker)
    
    print("--- History 1d ---")
    hist = dat.history(period="1d")
    print(hist.tail())
    
    print("\n--- Fast Info ---")
    try:
        print(f"Last Price: {dat.fast_info['last_price']}")
        print(f"Prev Close: {dat.fast_info['previous_close']}")
    except:
        print("Fast info failed/unavailable")
        
    print("\n--- Info ---")
    try:
        print(f"Current Price: {dat.info.get('currentPrice')}")
        print(f"Regular Market Previous Close: {dat.info.get('regularMarketPreviousClose')}")
        print(f"Previous Close: {dat.info.get('previousClose')}")
    except:
         print("Info failed")

if __name__ == "__main__":
    test()
