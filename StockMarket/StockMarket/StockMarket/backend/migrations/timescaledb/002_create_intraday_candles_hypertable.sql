-- ============================================================================
-- TimescaleDB Migration - Step 2: 10-Minute Intraday Candles Hypertable
-- ============================================================================
-- Purpose: Create optimized time-series table for 10-min OHLCV data
-- Run: psql -U postgres -d stock_data -f 002_create_intraday_candles_hypertable.sql
-- ============================================================================

-- Enable TimescaleDB extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Create 10-minute intraday candles table
CREATE TABLE IF NOT EXISTS intraday_candles_10m (
    time TIMESTAMPTZ NOT NULL,
    ticker_id SMALLINT NOT NULL REFERENCES tickers(ticker_id) ON DELETE CASCADE,
    open REAL NOT NULL,      -- 4 bytes vs 8 bytes (DOUBLE) = 50% savings
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL, -- 4 bytes vs 8 bytes (BIGINT) = 50% savings
    
    -- Composite primary key for uniqueness
    PRIMARY KEY (ticker_id, time)
);

-- Convert to hypertable (TimescaleDB magic!)
-- Chunks: 7-day time partitions, 4 ticker_id partitions
SELECT create_hypertable(
    'intraday_candles_10m',
    'time',
    chunk_time_interval => INTERVAL '7 days',
    partitioning_column => 'ticker_id',
    number_partitions => 4,
    if_not_exists => TRUE
);

-- ============================================================================
-- Indexes for Fast Queries
-- ============================================================================

-- Index for ticker-specific time-range queries (most common)
CREATE INDEX IF NOT EXISTS idx_intraday_10m_ticker_time 
    ON intraday_candles_10m (ticker_id, time DESC);

-- Index for time-based scans
CREATE INDEX IF NOT EXISTS idx_intraday_10m_time 
    ON intraday_candles_10m (time DESC);

-- ============================================================================
-- Set Table Storage Options
-- ============================================================================

-- Optimize for time-series access patterns
ALTER TABLE intraday_candles_10m SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'time DESC',
    timescaledb.compress_segmentby = 'ticker_id'
);

-- Set autovacuum aggressively for time-series data
ALTER TABLE intraday_candles_10m SET (
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_analyze_scale_factor = 0.01
);

-- ============================================================================
-- Verification
-- ============================================================================

-- Check hypertable creation
SELECT 
    hypertable_name,
    chunk_time_interval,
    num_dimensions
FROM timescaledb_information.hypertables
WHERE hypertable_name = 'intraday_candles_10m';

-- Check table structure
\d intraday_candles_10m;

-- Sample query to verify (will be empty initially)
SELECT 
    COUNT(*) as total_candles,
    COUNT(DISTINCT ticker_id) as unique_tickers,
    MIN(time) as oldest_candle,
    MAX(time) as newest_candle
FROM intraday_candles_10m;
