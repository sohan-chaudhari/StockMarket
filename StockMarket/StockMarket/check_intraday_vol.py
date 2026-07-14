
import yfinance as yf

def check_intra(ticker):
    print(f"\n--- {ticker} Intraday ---")
    t = yf.Ticker(ticker)
    
    # 1m
    print("Fetching 1m...")
    h1 = t.history(period="1d", interval="1m")
    if not h1.empty:
        print(f"1m Rows: {len(h1)}")
        print(f"1m Vol Sum: {h1['Volume'].sum()}")
        print(f"1m Last Vol: {h1.iloc[-1]['Volume']}")
    else:
        print("1m Empty")

    # 5m
    print("Fetching 5m...")
    h5 = t.history(period="1d", interval="5m")
    if not h5.empty:
        print(f"5m Rows: {len(h5)}")
        print(f"5m Vol Sum: {h5['Volume'].sum()}")
    else:
        print("5m Empty")

check_intra("^NSEBANK")
check_intra("^BSESN")
