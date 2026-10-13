-- v008: persistent historical backfill jobs (exact-range 5m gap repair).
-- Idempotent: safe to re-run. Also created at runtime by backfill_jobs.ensure_table(),
-- so a deployment does not depend on a migration runner.

CREATE TABLE IF NOT EXISTS backfill_jobs (
    id               BIGSERIAL PRIMARY KEY,
    ticker           VARCHAR(32)  NOT NULL,
    exchange         VARCHAR(8)   NOT NULL DEFAULT 'NSE',
    interval         VARCHAR(8)   NOT NULL,
    range_start      TIMESTAMP    NOT NULL,
    range_end        TIMESTAMP    NOT NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'queued',
    attempts         INTEGER      NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMP,
    last_attempt_at  TIMESTAMP,
    lease_expires_at TIMESTAMP,
    claimed_by       VARCHAR(64),
    last_error       VARCHAR(400),
    failure_kind     VARCHAR(24),
    expected_count   INTEGER,
    fetched_count    INTEGER,
    persisted_count  INTEGER,
    remaining_count  INTEGER,
    created_at       TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- One OPEN job per (ticker, exchange, interval, range): makes enqueue idempotent
-- and prevents duplicate/overlapping submissions.
CREATE UNIQUE INDEX IF NOT EXISTS uix_backfill_jobs_open
    ON backfill_jobs (ticker, exchange, interval, range_start, range_end)
    WHERE status IN ('queued','running','retry_wait','partially_complete');

-- Claim path.
CREATE INDEX IF NOT EXISTS ix_backfill_jobs_claimable
    ON backfill_jobs (status, next_attempt_at);

-- Per-ticker lookups / diagnostics.
CREATE INDEX IF NOT EXISTS ix_backfill_jobs_ticker
    ON backfill_jobs (ticker, interval);
