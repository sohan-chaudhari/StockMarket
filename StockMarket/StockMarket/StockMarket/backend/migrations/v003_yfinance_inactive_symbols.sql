-- v003_yfinance_inactive_symbols.sql
-- Adds is_active column to stock_metadata for tracking delisted/invalid symbols

ALTER TABLE stock_metadata ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

-- Index for querying active/inactive stocks
CREATE INDEX IF NOT EXISTS idx_stock_metadata_is_active ON stock_metadata (is_active);
