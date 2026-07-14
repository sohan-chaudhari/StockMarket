-- ============================================================================
-- TimescaleDB Migration - Step 3: Enable Native Compression
-- ============================================================================
-- Purpose: Achieve 90-95% storage reduction on historical data
-- Run: psql -U postgres -d stock_data -f 003_enable_compression.sql
-- ============================================================================

-- ============================================================================
-- Compression Policy: Compress data older than 2 days
-- ============================================================================
-- Reasoning:
-- - Recent data (< 2 days): Frequently updated, keep uncompressed
-- - Old data (> 2 days): Immutable, perfect for compression
-- ============================================================================

SELECT add_compression_policy(
    'intraday_candles_10m',
    INTERVAL '2 days',
    if_not_exists => TRUE
);

-- ============================================================================
-- Manual Compression (for testing)
-- ============================================================================
-- Uncomment to manually compress all chunks older than 2 days now

/*
SELECT compress_chunk(chunk)
FROM timescaledb_information.chunks
WHERE hypertable_name = 'intraday_candles_10m'
AND range_end < NOW() - INTERVAL '2 days';
*/

-- ============================================================================
-- Verification: Check Compression Status
-- ============================================================================

-- Show compression policy
SELECT *
FROM timescaledb_information.jobs
WHERE proc_name = 'policy_compression'
AND hypertable_name = 'intraday_candles_10m';

-- Show compressed chunks
SELECT 
    chunk_name,
    pg_size_pretty(before_compression_total_bytes) as before_size,
    pg_size_pretty(after_compression_total_bytes) as after_size,
    ROUND(
        100 - (after_compression_total_bytes::FLOAT / 
               NULLIF(before_compression_total_bytes, 0) * 100), 
        2
    ) as compression_ratio_pct
FROM timescaledb_information.compressed_chunk_stats
WHERE hypertable_name = 'intraday_candles_10m'
ORDER BY chunk_name DESC;

-- Total storage summary
SELECT 
    hypertable_name,
    pg_size_pretty(
        SUM(before_compression_total_bytes)
    ) as uncompressed_size,
    pg_size_pretty(
        SUM(after_compression_total_bytes)
    ) as compressed_size,
    ROUND(
        100 - (SUM(after_compression_total_bytes)::FLOAT / 
               NULLIF(SUM(before_compression_total_bytes), 0) * 100),
        2
    ) as avg_compression_pct
FROM timescaledb_information.compressed_chunk_stats
WHERE hypertable_name = 'intraday_candles_10m'
GROUP BY hypertable_name;

-- ============================================================================
-- Notes
-- ============================================================================
-- Expected compression ratio for OHLCV data: 90-95%
-- Example: 1.7 GB uncompressed → 170 MB compressed
-- 
-- Compression happens automatically via background job every hour
-- Recent data stays uncompressed for fast writes
-- Queries automatically decompress on-the-fly (transparent to application)
-- ============================================================================
