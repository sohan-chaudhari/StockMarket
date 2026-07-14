import yfinance as yf

# Try different ticker formats
tickers_to_try = ['RELIANCE.NS', 'RELIANCE.BO', 'RELIANCE', '500325.BO']

for t in tickers_to_try:
    print(f"\nTrying: {t}")
    try:
        df = yf.download(t, start='2020-01-01', end='2021-01-01', progress=False, auto_adjust=False)
        print(f"  Rows: {len(df)}")
        if len(df) > 0:
            print(f"  First: {df.index[0].date()}, Last: {df.index[-1].date()}")
    except Exception as e:
        print(f"  Error: {e}")
