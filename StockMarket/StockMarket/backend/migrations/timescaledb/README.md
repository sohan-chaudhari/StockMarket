# TimescaleDB Migration - Quick Reference Guide

## 📁 Files Created

### SQL Migrations (`backend/migrations/timescaledb/`)
1. `001_create_tickers_table.sql` - Ticker lookup with SMALLINT IDs
2. `002_create_intraday_candles_hypertable.sql` - 10-min candles table
3. `003_enable_compression.sql` - 90-95% compression policy
4. `004_create_continuous_aggregates.sql` - Auto 1h/1d rollups

### Python Scripts (`backend/`)
1. `migrate_tickers.py` - Populate tickers table
2. `backfill_intraday.py` - Fetch historical data from Angel One
3. `models_timescale.py` - SQLAlchemy models
4. `api_endpoints_timescale.py` - FastAPI endpoints

---

## 🚀 Installation Steps

### 1. Install TimescaleDB (Windows)

```powershell
# Download TimescaleDB installer from:
# https://docs.timescale.com/self-hosted/latest/install/installation-windows/

# Or use WSL2 + Ubuntu:
wsl --install -d Ubuntu
wsl

# Inside WSL:
sudo apt-get update
sudo apt-get install postgresql-15 postgresql-15-timescaledb-2.13.0

# Enable TimescaleDB
echo "shared_preload_libraries = 'timescaledb'" | sudo tee -a /etc/postgresql/15/main/postgresql.conf

# Restart PostgreSQL
sudo service postgresql restart
```

### 2. Run SQL Migrations

```powershell
cd c:\Users\rahul\Desktop\StockMarket\StockMarket\backend\migrations\timescaledb

# Connect to PostgreSQL (adjust credentials)
psql -U postgres -d stock_data

# Inside psql, run each migration:
\i 001_create_tickers_table.sql
\i 002_create_intraday_candles_hypertable.sql
\i 003_enable_compression.sql
\i 004_create_continuous_aggregates.sql

# Verify
SELECT * FROM timescaledb_information.hypertables;
\q
```

### 3. Migrate Tickers

```powershell
cd c:\Users\rahul\Desktop\StockMarket\StockMarket\backend

# Run ticker migration
python migrate_tickers.py

# Expected output:
# ✅ Migrated 1547 tickers
# ✅ All IDs within SMALLINT range
```

### 4. Backfill Historical Data

```powershell
# Test with a few stocks first
python backfill_intraday.py --tickers RELIANCE.NS,TCS.NS,HDFCBANK.NS --days 365

# Full backfill (overnight job)
python backfill_intraday.py --all --days 365 --workers 10
```

---

## 📊 API Usage Examples

### Get 10-minute candles (last 7 days)
```javascript
fetch('http://127.0.0.1:8000/api/timescale/candles/intraday?ticker=RELIANCE.NS&interval=10m&days=7')
  .then(res => res.json())
  .then(candles => console.log(candles));
```

### Get 1-hour candles (last 30 days)
```javascript
fetch('http://127.0.0.1:8000/api/timescale/candles/intraday?ticker=TCS.NS&interval=1h&days=30')
  .then(res => res.json())
  .then(candles => console.log(candles));
```

### Get daily candles (last year)
```javascript
fetch('http://127.0.0.1:8000/api/timescale/candles/intraday?ticker=INFY.NS&interval=1d&days=365')
  .then(res => res.json())
  .then(candles => console.log(candles));
```

### Check compression stats
```javascript
fetch('http://127.0.0.1:8000/api/timescale/stats/compression')
  .then(res => res.json())
  .then(stats => console.log(stats));
// Output: { compression_ratio: 92.5, space_saved: "92.5%" }
```

---

## 🔍 Monitoring Queries

### Check storage usage
```sql
SELECT pg_size_pretty(pg_total_relation_size('intraday_candles_10m'));
```

### Check compression effectiveness
```sql
SELECT 
    pg_size_pretty(before_compression_total_bytes) as before,
    pg_size_pretty(after_compression_total_bytes) as after,
    ROUND(100 - (after_compression_total_bytes::FLOAT / 
                 before_compression_total_bytes * 100), 2) as saved_pct
FROM timescaledb_information.compressed_chunk_stats
WHERE hypertable_name = 'intraday_candles_10m';
```

### Check candle counts per ticker
```sql
SELECT 
    t.ticker,
    COUNT(*) as candle_count,
    MIN(c.time) as oldest,
    MAX(c.time) as newest
FROM intraday_candles_10m c
JOIN tickers t ON c.ticker_id = t.ticker_id
GROUP BY t.ticker
ORDER BY candle_count DESC
LIMIT 10;
```

---

## 🎯 Expected Performance

| Metric | Before | After |
|--------|--------|-------|
| Storage (1000 stocks, 1 year) | 9 GB | **50 MB** |
| Query time (1-day chart) | 500ms | **80ms** |
| Data granularity | Daily | **10-minute** |

---

## 🔧 Troubleshooting

### Issue: "TimescaleDB extension not found"
**Solution:**
```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

### Issue: "Angel One rate limit exceeded"
**Solution:** Reduce `--workers` parameter:
```bash
python backfill_intraday.py --all --days 365 --workers 3
```

### Issue: "Out of SMALLINT range (> 32767)"
**Solution:** If you have more than 32,000 tickers, change to INTEGER:
```sql
ALTER TABLE tickers ALTER COLUMN ticker_id TYPE INTEGER;
ALTER TABLE intraday_candles_10m ALTER COLUMN ticker_id TYPE INTEGER;
```

---

## 📝 Next Steps After Migration

1. **Update frontend `dashboard.js`** to use new API endpoints
2. **Add interval selector** (10m, 1h, 1d) to chart UI
3. **Replace `current_day_candle` logic** with TimescaleDB writes
4. **Set up daily backfill cron job** (6 PM IST)
5. **Monitor compression ratios** weekly

---

## 📞 Rollback Plan

If issues arise:
```sql
-- Drop TimescaleDB tables (old schema still works)
DROP TABLE IF EXISTS intraday_candles_10m CASCADE;
DROP TABLE IF EXISTS tickers CASCADE;
DROP MATERIALIZED VIEW IF EXISTS candles_1h CASCADE;
DROP MATERIALIZED VIEW IF EXISTS candles_1d CASCADE;
```

Your existing `stock_data` and `current_day_candle` tables remain untouched!
