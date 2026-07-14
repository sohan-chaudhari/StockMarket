import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

import pandas as pd
import yfinance as yf
ticker = '^NSEI'
df = yf.download(ticker, period='5d', interval='1d', progress=False, auto_adjust=True)
print('Columns:', df.columns.tolist())
if isinstance(df.columns, pd.MultiIndex):
    df2 = df.xs('^NSEI', axis=1, level=1)
else:
    df2 = df.copy()
for idx, row in df2.iterrows():
    dt = idx.date() if hasattr(idx, 'date') else idx
    print(f'{dt}: O={row["Open"]:.2f} H={row["High"]:.2f} L={row["Low"]:.2f} C={row["Close"]:.2f}')
print('Shape:', df.shape)
