import yfinance as yf
from datetime import datetime, timedelta
from sqlalchemy import text
from database import SessionLocal

START = datetime.now() - timedelta(days=30)
END = datetime.now()

INDICES = {
    '^NSEI': 'NIFTY',
    '^NSEBANK': 'BANKNIFTY',
    '^BSESN': 'SENSEX'
}

session = SessionLocal()
for yf_symbol, db_ticker in INDICES.items():
    print(f'Fetching {db_ticker} via {yf_symbol}...')
    data = yf.download(yf_symbol, start=START, end=END, interval='5m', progress=False)
    if not data.empty:
        data.columns = data.columns.droplevel(1)
        valid = []
        for idx, row in data.iterrows():
            try:
                ts = idx.to_pydatetime()
                if ts.tzinfo: ts = ts.replace(tzinfo=None)
                valid.append({'ticker': db_ticker, 'timestamp': ts, 'open': float(row['Open']), 'high': float(row['High']), 'low': float(row['Low']), 'close': float(row['Close']), 'volume': int(row['Volume'])})
            except Exception as e:
                pass
        if valid:
            stmt = text('INSERT INTO intraday_candles_5min (ticker, timestamp, open, high, low, close, volume) VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume) ON CONFLICT (ticker, timestamp) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume')
            session.execute(stmt, valid)
            session.commit()
            print(f'Fetched and inserted {len(valid)} for {db_ticker}')
    else: print(f'No data for {db_ticker}')

