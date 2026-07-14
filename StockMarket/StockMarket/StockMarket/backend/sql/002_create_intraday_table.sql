CREATE TABLE IF NOT EXISTS intraday_candles_5min (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT,
    UNIQUE (ticker, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_intraday_ticker ON intraday_candles_5min(ticker);
CREATE INDEX IF NOT EXISTS idx_intraday_timestamp ON intraday_candles_5min(timestamp);
