import yfinance as yf
t = yf.Ticker("RELIANCE.NS")
info = t.fast_info
for attr in dir(info):
    if not attr.startswith('_'):
        try:
            print(f"{attr}: {getattr(info, attr)}")
        except:
             pass
