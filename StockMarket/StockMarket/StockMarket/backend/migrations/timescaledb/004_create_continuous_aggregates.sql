-- ============================================================================
-- TimescaleDB Migration - Step 4: Continuous Aggregates
-- ============================================================================
-- Purpose: Auto-generate 1-hour and 1-day candles from 10-min base data
-- Run: psql -U postgres -d stock_data -f 004_create_continuous_aggregates.sql
-- ============================================================================

-- ============================================================================
-- 1-Hour Candles (Materialized View)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS candles_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    ticker_id,
    first(open, time) AS open,      -- First open in the hour
    max(high) AS high,              -- Highest high
    min(low) AS low,                -- Lowest low
    last(close, time) AS close,     -- Last close in the hour
    sum(volume) AS volume           -- Total volume
FROM intraday_candles_10m
GROUP BY bucket, ticker_id
WITH NO DATA;

-- Add refresh policy: Update hourly aggregate every hour
-- start_offset: Look back 3 hours to catch late data
-- end_offset: Don't aggregate the current hour (still filling)
SELECT add_continuous_aggregate_policy(
    'candles_1h',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Create index for fast queries
CREATE INDEX IF NOT EXISTS idx_candles_1h_ticker_bucket 
    ON candles_1h (ticker_id, bucket DESC);

-- ============================================================================
-- 1-Day Candles (Materialized View)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS candles_1d
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS bucket,
    ticker_id,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM intraday_candles_10m
GROUP BY bucket, ticker_id
WITH NO DATA;

-- Add refresh policy: Update daily aggregate every 6 hours
SELECT add_continuous_aggregate_policy(
    'candles_1d',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '6 hours',
    if_not_exists => TRUE
);

-- Create index for fast queries
CREATE INDEX IF NOT EXISTS idx_candles_1d_ticker_bucket 
    ON candles_1d (ticker_id, bucket DESC);

-- ============================================================================
-- Helper Views: Join with Ticker Names
-- ============================================================================

-- 1-hour candles with ticker symbols
CREATE OR REPLACE VIEW candles_1h_view AS
SELECT 
    c.bucket as time,
    t.ticker,
    t.name,
    c.open,
    c.high,
    c.low,
    c.close,
    c.volume
FROM candles_1h c
JOIN tickers t ON c.ticker_id = t.ticker_id;

-- 1-day candles with ticker symbols
CREATE OR REPLACE VIEW candles_1d_view AS
SELECT 
    c.bucket as time,
    t.ticker,
    t.name,
    c.open,
    c.high,
    c.low,
    c.close,
    c.volume
FROM candles_1d c
JOIN tickers t ON c.ticker_id = t.ticker_id;

-- ============================================================================
-- Manual Refresh (for testing)
-- ============================================================================
-- Uncomment to manually refresh aggregates now

/*
CALL refresh_continuous_aggregate('candles_1h', NULL, NULL);
CALL refresh_continuous_aggregate('candles_1d', NULL, NULL);
*/

-- ============================================================================
-- Verification
-- ============================================================================

-- Check continuous aggregate policies
SELECT 
    view_name,
    schedule_interval,
    config
FROM timescaledb_information.jobs
WHERE proc_name = 'policy_refresh_continuous_aggregate'
ORDER BY view_name;

-- Sample query: Get 1-hour candles for RELIANCE (last 7 days)
/*
SELECT 
    bucket as time,
    open, high, low, close, volume
FROM candles_1h c
JOIN tickers t ON c.ticker_id = t.ticker_id
WHERE t.ticker = 'RELIANCE.NS'
AND bucket >= NOW() - INTERVAL '7 days'
ORDER BY bucket DESC
LIMIT 10;
*/

-- Check storage savings vs storing separately
SELECT 
    'intraday_candles_10m' as table_name,
    pg_size_pretty(pg_total_relation_size('intraday_candles_10m')) as size
UNION ALL
SELECT 
    'candles_1h' as table_name,
    pg_size_pretty(pg_total_relation_size('candles_1h')) as size
UNION ALL
SELECT 
    'candles_1d' as table_name,
    pg_size_pretty(pg_total_relation_size('candles_1d')) as size;

-- ============================================================================
-- Notes
-- ============================================================================
-- Benefits:
-- ✅ Single source of truth (10-min data)
-- ✅ Automatic aggregation (no manual rollups)
-- ✅ Fast queries (pre-computed)
-- ✅ Space efficient (materialized views optimized)
--
-- The aggregates refresh automatically via background jobs
-- No need to store 1H/1D data separately - it's derived from 10M base!
-- ============================================================================
