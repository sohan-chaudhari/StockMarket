-- v006_retention_jobs.sql
-- Creates the retention_jobs table for tracking historical data conversions.
-- Run: psql -U postgres -d stock_data -f v006_retention_jobs.sql

BEGIN;

CREATE TABLE IF NOT EXISTS retention_jobs (
    id              SERIAL PRIMARY KEY,
    job_type        VARCHAR(50) NOT NULL,       -- '5m_to_15m', '15m_to_30m', etc.
    status          VARCHAR(20) NOT NULL DEFAULT 'RUNNING',  -- RUNNING, COMPLETED, FAILED, SKIPPED
    start_time      TIMESTAMP   NOT NULL,
    end_time        TIMESTAMP,
    last_ticker     VARCHAR(50),                 -- resume point after crash
    rows_source     BIGINT DEFAULT 0,
    rows_target     BIGINT DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_retention_jobs_status
    ON retention_jobs (status, start_time);

COMMIT;
