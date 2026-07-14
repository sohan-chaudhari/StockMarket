import yfinance as yf

indices = ['^NSEI', '^NSEBANK', '^BSESN']

for tick in indices:
    print(f"\n--- {tick} ---")
    dat = yf.Ticker(tick)
    info = dat.fast_info
    print(f"LastPrice: {info.last_price}")
    print(f"Open: {info.open}")
    print(f"PrevClose: {info.previous_close}")
    
    # Try history as fallback
    hist = dat.history(period='1d')
    if not hist.empty:
        print(f"History Open: {hist['Open'].iloc[0]}")
    else:
        print("History empty")
