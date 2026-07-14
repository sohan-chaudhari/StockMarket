-- v002: Materialized views for 30m and 1h intraday candle aggregation
-- Run: psql -d yourdb -f v002_materialized_views.sql
-- Refresh: REFRESH MATERIALIZED VIEW CONCURRENTLY intraday_candles_30min;

CREATE MATERIALIZED VIEW IF NOT EXISTS intraday_candles_30min AS
SELECT ticker,
       date_trunc('hour', timestamp)
         + INTERVAL '30 min' * FLOOR(EXTRACT(MINUTE FROM timestamp) / 30) AS bucket,
       (array_agg(open  ORDER BY timestamp))[1] AS open,
       MAX(high)  AS high,
       MIN(low)   AS low,
       (array_agg(close ORDER BY timestamp DESC))[1] AS close,
       SUM(volume) AS volume
FROM intraday_candles_5min
GROUP BY ticker, bucket
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_30min_ticker_bucket ON intraday_candles_30min (ticker, bucket);

CREATE MATERIALIZED VIEW IF NOT EXISTS intraday_candles_1h AS
SELECT ticker,
       date_trunc('hour', timestamp) AS bucket,
       (array_agg(open  ORDER BY timestamp))[1] AS open,
       MAX(high)  AS high,
       MIN(low)   AS low,
       (array_agg(close ORDER BY timestamp DESC))[1] AS close,
       SUM(volume) AS volume
FROM intraday_candles_5min
GROUP BY ticker, bucket
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_1h_ticker_bucket ON intraday_candles_1h (ticker, bucket);

-- Refresh strategy: after every 5m candle insert batch, run:
-- REFRESH MATERIALIZED VIEW CONCURRENTLY intraday_candles_30min;
-- REFRESH MATERIALIZED VIEW CONCURRENTLY intraday_candles_1h;
-- (or use pg_cron for periodic refresh)
