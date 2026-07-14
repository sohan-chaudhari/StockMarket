from datetime import datetime, timedelta
from sqlalchemy import text
from database import SessionLocal
from historical_service import historical_service

historical_service.login()
START = datetime.now() - timedelta(days=30)
END = datetime.now()

INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'BANKEX', 'MIDCPNIFTY']

session = SessionLocal()
for ticker in INDICES:
    candles = historical_service.get_historical_candles(ticker, 'FIVE_MINUTE', START, END)
    if candles:
        valid = []
        for c in candles:
            try:
                ts = c['timestamp']
                if ts.tzinfo: ts = ts.replace(tzinfo=None)
                valid.append({'ticker': ticker, 'timestamp': ts, 'open': float(c['open']), 'high': float(c['high']), 'low': float(c['low']), 'close': float(c['close']), 'volume': int(c['volume'])})
            except: pass
        if valid:
            stmt = text('INSERT INTO intraday_candles_5min (ticker, timestamp, open, high, low, close, volume) VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume) ON CONFLICT (ticker, timestamp) DO NOTHING')
            session.execute(stmt, valid)
            session.commit()
            print(f'Fetched {len(valid)} for {ticker}')
    else: print(f'No data for {ticker}')

