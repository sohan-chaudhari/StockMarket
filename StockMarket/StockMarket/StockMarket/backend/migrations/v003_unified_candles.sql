-- v003_unified_candles.sql
-- Creates the unified `candles` table that replaces separate 1m/5m/15m tables.
-- Run: psql -U postgres -d stock_data -f v003_unified_candles.sql

BEGIN;

-- ── 1. Create unified candles table ──────────────────────────────────
CREATE TABLE IF NOT EXISTS candles (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(50) NOT NULL,
    timeframe       VARCHAR(5)  NOT NULL,   -- '1m','5m','15m','30m','1h'
    timestamp       TIMESTAMP   NOT NULL,
    open            DOUBLE PRECISION NOT NULL,
    high            DOUBLE PRECISION NOT NULL,
    low             DOUBLE PRECISION NOT NULL,
    close           DOUBLE PRECISION NOT NULL,
    volume          BIGINT DEFAULT 0,
    is_completed    BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),

    -- Prevent duplicate candles for same ticker/timeframe/timestamp
    CONSTRAINT uix_candle_key UNIQUE (ticker, timeframe, timestamp)
);

-- Composite index for fast lookups
CREATE INDEX IF NOT EXISTS idx_candle_lookup
    ON candles (ticker, timeframe, timestamp);

-- Index for queries by timeframe + timestamp range
CREATE INDEX IF NOT EXISTS idx_candle_tf_ts
    ON candles (timeframe, timestamp);

-- ── 2. Migrate existing data from legacy tables ─────────────────────
-- IntradayCandle1Min
INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, created_at)
SELECT ticker, '1m', timestamp, open, high, low, close, COALESCE(volume, 0), TRUE, NOW()
FROM intraday_candles_1min
ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING;

-- IntradayCandle5Min
INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, created_at)
SELECT ticker, '5m', timestamp, open, high, low, close, COALESCE(volume, 0), TRUE, NOW()
FROM intraday_candles_5min
ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING;

-- IntradayCandle15Min
INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, created_at)
SELECT ticker, '15m', timestamp, open, high, low, close, COALESCE(volume, 0), TRUE, NOW()
FROM intraday_candles_15min
ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING;

-- ── 3. (Optional) Drop legacy tables after verifying migration ───────
-- Uncomment only after confirming data is correct:
-- DROP TABLE IF EXISTS intraday_candles_1min;
-- DROP TABLE IF EXISTS intraday_candles_5min;
-- DROP TABLE IF EXISTS intraday_candles_15min;
-- DROP TABLE IF EXISTS intraday_candles_30min;

COMMIT;
