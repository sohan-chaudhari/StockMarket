# Stock Market System Report

## 1. System Overview

| Component | Description |
|-----------|-------------|
| Backend | FastAPI (Python) |
| Database | PostgreSQL via SQLAlchemy |
| Real-time | AngelOne WebSocket |
| Fallback | yfinance (on-demand) |
| Total tickers | 5,543 NSE stocks |

---

## 2. Live Candle Building

**All 5,543 tickers** get live candle building during market hours when viewed. Up to **500 concurrent WS subscriptions** (AngelOne cap) with LRU eviction — the least-recently-viewed ticker with no active viewers is evicted first. Evicted tickers auto-resubscribe when viewed again.

### How it works:
1. User opens a chart → frontend sends `view_ticker` WS message
2. `ViewedTickerManager` subscribes to AngelOne WS for that ticker
3. Aggregator builds 1m/5m/15m/30m/1h/1D/1W/1M candles **in memory**
4. After 5 min of no viewers → auto-unsubscribe from AngelOne
5. **WS reconnect recovery**: watchdog detects disconnect → fetches missed 1m bars from yfinance → replays into aggregator

### Data loss minimized:
- WS tick → aggregator.process_tick() → updates all forming candles
- WS broker SDK maintains internal buffer; on reconnect it replays missed ticks
- Watchdog recovery: if a ticker is stale >2 min after WS reconnect, fetches missed 1m bars from yfinance and replays into aggregator

---

## 3. Database Storage Breakdown

### Current State (as of today)

| Group | Count | Candle Data Stored | Retention |
|-------|:----:|--------------------|-----------|
| **A: Premium (full intraday)** | 213 | 1m + 5m + 15m + 30m + 1h + 1D + 1W + 1M | 1m:7d, 5m:30d, 15m:60d, 30m/1h:90d, 1D:365d, 1W/1M:730d |
| **B: Standard (intraday 1 day)** | 581 | All intraday TFs + 1D/1W/1M | Intraday:1d, 1D:365d, 1W/1M:730d |
| **C: On-demand** | 4,749 | Intraday (when viewed) + 1D (via daily prefill) | Intraday:1d, 1D:365d, 1W/1M:730d |
| **Total** | **5,543** | | |

### Storage Per Day

| Group | 1m | 5m | 15m | 30m | 1h | 1D | Total/day |
|:------|:--:|:--:|:---:|:---:|:--:|:--:|:---------:|
| A (213) | 79,875 | 15,975 | 5,325 | 2,556 | 1,278 | 213 | **105,222** |
| B (581) | 217,875 | 43,575 | 14,525 | 6,972 | 3,486 | 581 | **287,014** |
| C (4,749) | —* | —* | —* | —* | —* | 4,749* | **~4,749** |
| **Total** | **297,750** | **59,550** | **19,850** | **9,528** | **4,764** | **5,543** | **396,985** |

*^ C-group intraday only produced when users view charts; daily always filled by prefill task*

### Steady-State Storage (after retention cleanup)

| Timeframe | Premium (213) | Standard (581) | All tickers (1D) | Total |
|:----------|:-------------:|:--------------:|:----------------:|:-----:|
| 1m (7d/1d) | 213×375×7 = 559K | 581×375×1 = 218K | — | 777K rows |
| 5m (30d/1d) | 213×75×30 = 479K | 581×75×1 = 44K | — | 523K rows |
| 15m (60d/1d) | 213×25×60 = 319K | 581×25×1 = 15K | — | 334K rows |
| 30m (90d/1d) | 213×12×90 = 230K | 581×12×1 = 7K | — | 237K rows |
| 1h (90d/1d) | 213×6×90 = 115K | 581×6×1 = 3K | — | 118K rows |
| 1D (365d) | 213×365 = 78K | — | 5,330×365 = 1,945K | 2,023K rows |
| 1W/1M (730d) | negligible | — | — | negligible |
| **Total rows** | | | | **~4.0M rows** |
| **Total storage** | | | | **~0.8 GB** |

*Note: Daily (1D) stored for all 5,543 tickers via prefill task. 1W/1M only exist for tickers that were viewed (negligible rows). Storage is ~200 bytes/row + ~50% index overhead.*

---

## 4. Solutions Implemented

| # | Solution | What it does |
|---|----------|-------------|
| 1 | **Tiered yfinance TTL** | 5 min (market) / 1h (off) / 6h (weekend) — reduces yfinance calls |
| 2 | **Per-ticker 60s cooldown** | No same-ticker yfinance fetch within 60s |
| 3 | **Pre-warm at market open** | Fetches all ~794 tickers into cache at 09:00 — instant first load |
| 4 | **WS LRU eviction** | 500-cap, auto-resubscribe, eviction logging |
| 5 | **ViewedTickerManager** | Reference-counted view tracking, 5-min unsub debounce |
| 6 | **Semaphore(3) yfinance** | Max 3 concurrent downloads prevents rate-limit blocks |
| 7 | **Nightly retention cleanup** | 02:00 IST, tiered per-group (premium vs standard) |
| 8 | **`is_premium` flag** | Dedicated `stock_metadata.is_premium` column (not fragile COUNT query) |
| 9 | **Daily prefill all 5,543** | Background task at 19:00 IST + startup — fills 1D for every ticker |
| 10 | **WS reconnect recovery** | Watchdog detects disconnect → fetches missed 1m bars → replays |
| 11 | **Monitoring & metrics** | `/api/health` with real-time counters for yfinance, WS, aggregator, cache, evictions |
| 12 | **Fix aggregator NameError crash** | `flushed_1h` → `completed_1h` (L594) — prevented hourly rollover crash that killed 1D volume updates |
| 13 | **Lock-safety for flush** | `_flush_batch_now()` moved outside `self._lock` — eliminates deadlock risk between aggregator and flush queue locks |
| 14 | **Full ticker recovery on restart** | Removed `.limit(200)` from `recover_from_db()` — all 794+ premium+standard tickers recover forming candle state after crash/restart |
| 15 | **Complete post-market flush** | All TFs (1m/5m/15m/30m/1h/1D/1W/1M) flushed before cleanup at market close — previously only 1D saved, intraday candles silently dropped |
| 16 | **Preserve open price on upsert** | `ON CONFLICT` set `"open": Candle.open` — backfilled open prices no longer overwritten by subsequent aggregator flushes |
| 17 | **GC rate-limited** | `_gc_inactive()` now runs max once per 60s (was every tick) — reduces O(n) CPU overhead inside the lock at high tick rates |
| 18 | **No phantom after-hours candles** | `recover_from_db()` only initializes forming candles during market hours — prevents stale candle state on restarts after 15:30 |
| 19 | **IST-correct health metrics** | `compute_candle_health()` now uses `ist_now_naive()` instead of `datetime.now()` — fixes 5.5h UTC offset in 24h candle count |

---

## 5. Retention Policy (Final)

### Premium tickers (213) — identified by `stock_metadata.is_premium = TRUE`
| Timeframe | Kept for | Rows steady-state |
|:----------|:--------:|:-----------------:|
| 1m | **7 days** | 559K |
| 5m | **30 days** | 479K |
| 15m | **60 days** | 319K |
| 30m | **90 days** | 230K |
| 1h | **90 days** | 115K |
| 1D | **365 days** | 78K |
| 1W/1M | **730 days** | ~1K |

### Standard tickers (581 + 4,749) — intraday kept 1 day only
| Timeframe | Kept for | Rows steady-state |
|:----------|:--------:|:-----------------:|
| 1m-1h | **1 day** | 287K |
| 1D | **365 days** | 1,945K |
| 1W/1M | **730 days** | ~17K |

### Steady-state total: **~0.8 GB** (vs 5.5 GB/year growing unbounded without cleanup)

---

## 6. Quick Reference

| Question | Answer |
|----------|--------|
| All 5,543 build live candles? | Yes, when viewed (max 500 simultaneous via LRU) |
| Data loss on WS gap? | Minimized — watchdog replays missed 1m bars (max 20 tickers) |
| 213 premium intraday stored? | Yes, all TFs with tiered retention |
| 581 standard intraday stored? | Yes, but deleted after 1 day |
| 4,749 on-demand stored? | Yes, same as standard (intraday 1d, daily persisted) |
| Daily candles for all 5,543? | Yes — prefill task runs daily at 19:00 IST |
| yfinance blocking risk? | Mitigated: semaphore(3) + 60s cooldown + 1h-6h TTL |
| WS cap exceeded? | LRU eviction with auto-resubscribe on re-view |
| DB steady-state size? | **~0.8 GB** (retention cleanup at 02:00 IST) |
| Premium detection method? | `stock_metadata.is_premium` column (213 tickers flagged) |
| WS disconnect recovery? | Watchdog replays yfinance 1m bars into aggregator |
| Can frontend view any ticker live? | Yes — dynamic WS sub via `view_ticker` message |
| Hourly rollover crash risk? | Fixed — `flushed_1h` NameError was crashing aggregator every hour; changed to `completed_1h` |
| All 794 active tickers recover on restart? | Fixed — removed `.limit(200)` from recover_from_db; now also filtered to only today's candles for speed |
| Post-market data loss? | Fixed — all TFs flushed before cleanup (was only 1D); intraday candles 15:25-15:30 no longer silently dropped |
| Open price corruption on upsert? | Fixed — `Candle.open` preserved on `ON CONFLICT` instead of unconditionally overwritten |
| GC overhead on every tick? | Fixed — rate-limited to once per 60s |

---

## 7. Key Files

| File | Purpose |
|------|---------|
| `backend/aggregator.py` | Candle aggregation engine — builds all TFs from 1m ticks |
| `backend/main.py` | API server, WS manager, background tasks, recovery |
| `backend/angelone_service.py` | AngelOne WS + REST client with unsubscribe support |
| `backend/models.py` | SQLAlchemy models (StockMetadata with is_premium) |
| `backend/migrations/v005_premium_tickers.sql` | SQL migration for is_premium column |
| `backend/data_inventory.csv` | 5,543 tickers with daily/intraday status |
