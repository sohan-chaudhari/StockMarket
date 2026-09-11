# ADR-008: Chart Data Pipeline

Status: Accepted

## Context

Frontend charts need candles across intraday ranges and daily/weekly/monthly
history, served fast, resilient to gaps, and consistent with live WS updates.

Verified facts:
- **Sources**, in resolution order for a chart request:
  1. `CandleCache` (candle_cache.py:41) — in-memory LRU with TTL, merged with
     the live timeframe manager's forming candle (`_live_mgr.get_current`).
  2. `Candle` table (`candles`) — completed 5m/higher for intraday, `1D`/`1W`/`1M`
     rows written by daily sync (main.py:4482, 4828) and retention.
  3. Separate `intraday_candles_1min/5min/15min/30min/1h` tables for backfilled
     intraday (models.py:53-91, 332-364).
  4. yfinance fallback with a failed-symbol cache (`_is_yfinance_failed` /
     `_mark_yfinance_failed`, main.py) and a shared background semaphore, used
     only when DB data is insufficient.
- `ChartService` (chart_service.py:9) orchestrates DB + live + resampler
  (`CandleResampler`), constructed in `init_recovery_service` (main.py:4891).
- `LiveTfBuilder` (live_timeframe_manager.py:72) maintains in-memory forming
  higher-TF candles from 5m events (`candle.5m.updated` / `candle.5m.completed`
  subscriptions at :249-251).
- Frontend also caches in IndexedDB; `tv-datafeed.js` uses the backend as its
  data source and pipes WS `price_update` events into `subscribeBars`.

## Decision

Layered read pipeline: cache → DB candles → separate intraday tables →
yfinance fallback. Live updates are layered on top from the in-memory forming
candle, never from the DB at tick cadence (ADR-001/ADR-002).

## Alternatives considered

- **Single monolithic history table**: rejected — intraday backfill and live
  candle storage have different write patterns (batch vs tick).
- **Always fetch from yfinance**: rejected — slow and rate-limited; DB-first
  with cache is required for dashboard load performance.

## Consequences

- The frontend must gracefully handle three cache tiers and a WS "forming"
  overlay; this is why the frontend chart code is complex.
- yfinance is a safety net, not the primary path — failed symbols are cached to
  avoid repeated slow timeouts.
- Resampled data (15m/30m/…) may come from either retention-written `candles`
  rows or on-the-fly resampling; both must produce NSE-aligned buckets
  (ADR-002) to stay consistent.

## Rollback considerations

No rollback beyond cache TTL / fallback toggles. Clearing the yfinance
failed-symbol cache just re-enables fallback retries.

## Related files

- `backend/chart_service.py` — `ChartService`
- `backend/candle_cache.py` — `CandleCache`
- `backend/live_timeframe_manager.py` — `LiveTfBuilder`
- `backend/main.py` — range/intraday endpoints, yfinance fallback,
  daily sync writers (:4482, :4828)
- `frontend/cache.js`, `frontend/tv-datafeed.js` — client side
