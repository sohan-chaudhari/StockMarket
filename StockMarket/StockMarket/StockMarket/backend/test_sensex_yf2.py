import yfinance as yf

# Get current data for SENSEX
ticker = yf.Ticker('^BSESN')
hist = ticker.history(period="5d")
print("Yahoo Finance ^BSESN History:")
print(hist[['Open', 'High', 'Low', 'Close']])
