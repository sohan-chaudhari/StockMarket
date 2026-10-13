-- v009: buckets that cannot be repaired, so they are never re-fetched.
-- Idempotent: safe to re-run. Also created at runtime by
-- backfill_jobs.ensure_table(), so a deployment does not depend on a migration
-- runner.
--
-- `reason` separates two very different things:
--   provider_absent -- PROVEN. A fetch SUCCEEDED and its returned span bracketed
--                      the bucket (data before AND after it) while omitting it,
--                      which rules out a truncated range. Never inferred from a
--                      count of empty responses.
--   pre_data        -- the bucket predates any observed data for the ticker (or
--                      its listing date when the schema provides one). Recorded
--                      as pre_data rather than provider_absent because
--                      stock_metadata has no listing_date column today, so a
--                      pre-listing period cannot be told apart from a gap.
--
-- Excluding these from missing_ranges() is what stops an illiquid ticker's
-- no-trade buckets from re-triggering a repair on every chart open. The buckets
-- are recorded with their reason and evidence -- never silently dropped.

CREATE TABLE IF NOT EXISTS backfill_absent (
    id           BIGSERIAL PRIMARY KEY,
    ticker       VARCHAR(32)  NOT NULL,
    exchange     VARCHAR(8)   NOT NULL DEFAULT 'NSE',
    interval     VARCHAR(8)   NOT NULL,
    bucket       TIMESTAMP    NOT NULL,
    reason       VARCHAR(24)  NOT NULL,
    evidence     VARCHAR(200),
    confirmed_at TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- One row per (ticker, exchange, interval, bucket): makes mark_absent idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS uix_backfill_absent
    ON backfill_absent (ticker, exchange, interval, bucket);

-- Lookup path used by missing_ranges().
CREATE INDEX IF NOT EXISTS ix_backfill_absent_lookup
    ON backfill_absent (ticker, interval, bucket);
