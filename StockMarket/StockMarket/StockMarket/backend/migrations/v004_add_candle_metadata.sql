-- v004: Add data_source and is_backfilled columns to candles table
ALTER TABLE candles ADD COLUMN IF NOT EXISTS data_source VARCHAR(10) DEFAULT 'ANGELONE';
ALTER TABLE candles ADD COLUMN IF NOT EXISTS is_backfilled BOOLEAN DEFAULT FALSE;
-- Update existing ANGELONE candles (no change needed, default already correct)
-- Update any YFINANCE candles that may exist
UPDATE candles SET data_source = 'YFINANCE' WHERE data_source IS NULL OR data_source = '';
-- Make sure NOT NULL for future rows
ALTER TABLE candles ALTER COLUMN data_source SET NOT NULL;
ALTER TABLE candles ALTER COLUMN is_backfilled SET NOT NULL;
