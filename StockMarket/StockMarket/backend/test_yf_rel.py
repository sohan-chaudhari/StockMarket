import yfinance as yf
import datetime
rel = yf.Ticker('RELIANCE.NS')
hist = rel.history(interval='5m', start='2026-07-04', end='2026-07-05')
print('Yfinance got', len(hist), 'candles for today!')
if len(hist)>0: print(hist.head(3)); print(hist.tail(3))
