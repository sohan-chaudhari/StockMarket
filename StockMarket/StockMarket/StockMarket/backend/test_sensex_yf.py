import yfinance as yf

# Get current data for SENSEX
ticker = yf.Ticker('^BSESN')
hist = ticker.history(period="5d")
print("Yahoo Finance ^BSESN History:")
print(hist[['Close']])
fast = ticker.fast_info
print("Previous Close:", fast.get('previousClose'))
print("Last Price:", fast.get('lastPrice'))
