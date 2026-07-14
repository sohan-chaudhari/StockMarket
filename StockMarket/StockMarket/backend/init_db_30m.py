from database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
db.execute(text('''CREATE TABLE IF NOT EXISTS intraday_candles_30min (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    open FLOAT,
    high FLOAT,
    low FLOAT,
    close FLOAT,
    volume INTEGER
)'''))
db.execute(text('CREATE INDEX IF NOT EXISTS ix_intraday_candles_30min_ticker ON intraday_candles_30min (ticker)'))
db.execute(text('CREATE INDEX IF NOT EXISTS ix_intraday_candles_30min_timestamp ON intraday_candles_30min (timestamp)'))
db.execute(text('CREATE INDEX IF NOT EXISTS idx_30min_ticker_ts ON intraday_candles_30min (ticker, timestamp)'))
try:
    db.execute(text('ALTER TABLE intraday_candles_30min ADD CONSTRAINT uix_ticker_timestamp_30min UNIQUE (ticker, timestamp)'))
except Exception as e:
    pass
db.commit()
print('Table created successfully')

