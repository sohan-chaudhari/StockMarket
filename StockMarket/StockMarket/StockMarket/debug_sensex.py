import yfinance as yf

# Test ^BSESN
print("Fetching ^BSESN...")
dat = yf.Ticker("^BSESN")
hist = dat.history(period="5d")
print(hist)

if not hist.empty:
    print("Latest:", hist.iloc[-1])
else:
    print("Empty history for ^BSESN")
