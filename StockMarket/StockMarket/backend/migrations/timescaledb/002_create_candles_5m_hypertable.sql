-- ============================================================================
-- TimescaleDB Migration - Revised Schema: 5-Minute Base Candles
-- ============================================================================
-- Purpose: Create single 5m hypertable, all timeframes derived via resampling
-- Run: psql -U postgres -d stock_data -f 002_create_candles_5m_hypertable.sql
-- ============================================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- Main Table: 5-Minute Candles (All Historical Data)
-- ============================================================================

CREATE TABLE IF NOT EXISTS candles_5m (
    time TIMESTAMPTZ NOT NULL,
    ticker_id SMALLINT NOT NULL REFERENCES tickers(ticker_id) ON DELETE CASCADE,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    
    PRIMARY KEY (ticker_id, time)
);

-- Convert to hypertable
-- 7-day chunks for efficient querying
SELECT create_hypertable(
    'candles_5m',
    'time',
    chunk_time_interval => INTERVAL '7 days',
    partitioning_column => 'ticker_id',
    number_partitions => 4,
    if_not_exists => TRUE
);

-- ============================================================================
-- Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_candles_5m_ticker_time 
    ON candles_5m (ticker_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_candles_5m_time 
    ON candles_5m (time DESC);

-- ============================================================================
-- Compression Settings
-- ============================================================================

ALTER TABLE candles_5m SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'time DESC',
    timescaledb.compress_segmentby = 'ticker_id'
);

-- Compress data older than 7 days (recent data changes frequently)
SELECT add_compression_policy(
    'candles_5m',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ============================================================================
-- Retention Policy (Optional)
-- ============================================================================
-- Keep only 2 years of 5m data in base table
-- Older data will be aggregated to higher timeframes

SELECT add_retention_policy(
    'candles_5m',
    INTERVAL '2 years',
    if_not_exists => TRUE
);

-- ============================================================================
-- Continuous Aggregates: 15-Minute Candles (2023-2025)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS candles_15m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('15 minutes', time) AS bucket,
    ticker_id,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM candles_5m
WHERE time >= '2023-01-01' AND time < '2025-01-01'
GROUP BY bucket, ticker_id
WITH NO DATA;

-- Refresh policy for 15m view
SELECT add_continuous_aggregate_policy(
    'candles_15m',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '6 hours',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_candles_15m_ticker_bucket 
    ON candles_15m (ticker_id, bucket DESC);

-- ============================================================================
-- Continuous Aggregates: 1-Hour Candles (2020-2023)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS candles_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    ticker_id,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume
FROM candles_5m
WHERE time >= '2020-01-01' AND time < '2023-01-01'
GROUP BY bucket, ticker_id
WITH NO DATA;

-- Refresh policy for 1h view
SELECT add_continuous_aggregate_policy(
    'candles_1h',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '12 hours',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_candles_1h_ticker_bucket 
    ON candles_1h (ticker_id, bucket DESC);

-- ============================================================================
-- Continuous Aggregates: 1-Day Candles (2015-2020)
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
FROM candles_5m
WHERE time >= '2015-01-01' AND time < '2020-01-01'
GROUP BY bucket, ticker_id
WITH NO DATA;

-- Refresh policy for 1d view
SELECT add_continuous_aggregate_policy(
    'candles_1d',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_candles_1d_ticker_bucket 
    ON candles_1d (ticker_id, bucket DESC);

-- ============================================================================
-- Helper Function: Smart Query Router
-- ============================================================================
-- Returns appropriate table/view based on date range

CREATE OR REPLACE FUNCTION get_optimal_candle_table(
    query_from_date TIMESTAMPTZ,
    query_to_date TIMESTAMPTZ
) RETURNS TEXT AS $$
DECLARE
    days_range INTEGER;
BEGIN
    days_range := EXTRACT(EPOCH FROM (query_to_date - query_from_date)) / 86400;
    
    -- If querying recent data (< 2 years), use 5m
    IF query_from_date >= NOW() - INTERVAL '2 years' THEN
        RETURN 'candles_5m';
    
    -- If querying 2023-2025 range, use 15m
    ELSIF query_from_date >= '2023-01-01' AND query_to_date < '2025-01-01' THEN
        RETURN 'candles_15m';
    
    -- If querying 2020-2023 range, use 1h
    ELSIF query_from_date >= '2020-01-01' AND query_to_date < '2023-01-01' THEN
        RETURN 'candles_1h';
    
    -- If querying 2015-2020 range, use 1d
    ELSIF query_from_date >= '2015-01-01' AND query_to_date < '2020-01-01' THEN
        RETURN 'candles_1d';
    
    -- Default to 5m for mixed ranges
    ELSE
        RETURN 'candles_5m';
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Verification
-- ============================================================================

-- Check hypertable
SELECT 
    hypertable_name,
    chunk_time_interval,
    num_dimensions
FROM timescaledb_information.hypertables
WHERE hypertable_name = 'candles_5m';

-- Check continuous aggregates
SELECT 
    view_name,
    materialization_hypertable_name,
    view_definition
FROM timescaledb_information.continuous_aggregates
ORDER BY view_name;

-- Check policies
SELECT 
    hypertable_name,
    proc_name,
    config
FROM timescaledb_information.jobs
WHERE hypertable_name IN ('candles_5m', 'candles_15m', 'candles_1h', 'candles_1d')
ORDER BY hypertable_name, proc_name;

-- ============================================================================
-- Notes
-- ============================================================================
-- Strategy:
-- - Store ALL data as 5m candles in candles_5m
-- - Automatic aggregation to 15m, 1h, 1d via continuous aggregates
-- - Query router suggests optimal table based on date range
-- - Compression kicks in after 7 days
-- - Retention deletes data older than 2 years from base table
--   (but aggregates remain)
-- ============================================================================
