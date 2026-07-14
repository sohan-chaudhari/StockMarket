-- ============================================================================
-- TimescaleDB Migration - Step 1: Ticker Lookup Table
-- ============================================================================
-- Purpose: Normalize ticker storage from VARCHAR to SMALLINT (87% space savings)
-- Run: psql -U postgres -d stock_data -f 001_create_tickers_table.sql
-- ============================================================================

-- Create tickers lookup table
CREATE TABLE IF NOT EXISTS tickers (
    ticker_id SMALLINT PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(255),
    exchange VARCHAR(10), -- 'NSE', 'BSE', 'INDEX'
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for fast ticker lookups
CREATE INDEX IF NOT EXISTS idx_tickers_ticker ON tickers(ticker);
CREATE INDEX IF NOT EXISTS idx_tickers_exchange ON tickers(exchange);

-- Create function to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to auto-update updated_at
CREATE TRIGGER update_tickers_updated_at
    BEFORE UPDATE ON tickers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Optional: Migrate existing tickers from stock_metadata
-- ============================================================================
-- Uncomment if you want to auto-populate from existing data

/*
INSERT INTO tickers (ticker_id, ticker, name, exchange)
SELECT 
    ROW_NUMBER() OVER (ORDER BY ticker)::SMALLINT as ticker_id,
    ticker,
    name,
    exchange
FROM stock_metadata
WHERE ticker IS NOT NULL
ON CONFLICT (ticker) DO NOTHING;
*/

-- ============================================================================
-- Verification
-- ============================================================================
SELECT 
    COUNT(*) as total_tickers,
    COUNT(DISTINCT exchange) as exchanges,
    pg_size_pretty(pg_total_relation_size('tickers')) as table_size
FROM tickers;
