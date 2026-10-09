# Session Summary

## Sessions
### Session 1: CSRF, Auth, and Safety Fixes (22 high + 20 low severity bugs)

**Goal:** Fix critical backend bugs (CSRF, race conditions, SQL ordering) and frontend bugs (parseInt radix, undefined variables).

**Files Modified:**
- `backend/main.py`, `backend/models.py`, `backend/execution_engine.py`, `backend/angelone_service.py`, `backend/main_from_view.py`, `backend/main_reconstructed.py`, `backend/fix_all.py`, `backend/backfill_history.py`, `backend/backfill_intraday_5min.py`, `backend/backfill_intraday_15min.py`
- `backend/routers/auth_router.py`
- `backend/__init__.py`, `backend/routers/__init__.py` (new)
- `frontend/stock-logic.js`, `frontend/index.js`, `frontend/portfolio.html`, `frontend/dashboard.js`, `frontend/nav.js`, `frontend/stock-ui.js`, `frontend/stock.html`, `frontend/stock_1_to_800.html`

**Bugs Fixed:**
- 7 CSRF/token issues, ORDER BY on leaderboard, mixed response model, list hashable, double-commit, batch limits, account lockout race, ForeignKeys on trades
- 8 bare except → except Exception
- parseInt radix, IST calc (UTC+5:30 hardcoded → correct), duplicate HTML, missing __init__.py, implicit globals, null safety

### Session 2: Chart Bugs, Caching Architecture, yfinance Fallback

**Goal:** Fix all chart/candle bugs, implement IndexedDB caching architecture, add yfinance fallback.

## Files Created
- `frontend/cache.js` — IndexedDB cache layer, memory LRU cache, storage budget, TTL management, background cleanup
- `backend/migrations/v002_materialized_views.sql` — 30m/1h materialized views

## Files Modified
- `backend/main.py` — yfinance fallback for range/intraday; new paginated endpoints
- `frontend/dashboard.js` — 3-tier cache in loadData(), _renderChartData() shared renderer, WS serverTs, WS reconnect recovery, NIFTY timestamp fix, ResizeObserver fix, duplicate global removal, localStorage 2MB cap
- `frontend/market.html` — 3-tier cache in loadData(), renderChartCached(), getChartCoordinateAPI(), cache.js script load
- `frontend/drawings.js` — initToolManager() chart pan/zoom subscriptions for overlay redraw
- `frontend/stock.html` — Added cache.js script tag
- `frontend/index.html`, `home.html`, `index_chart.html`, `chart.html`, `stock_rebuilt.html` — Added cache.js script tag
- `rebuild_stock.py` — Added cache.js to generated HTML

## Bugs Fixed (Chart/Candle)
1. **NIFTY UTC/IST (B6)**: `toTimeNum` YYYY-MM-DD → UTC parsing fixed
2. **Forming candle flicker (B10)**: `_pendingRange` guard
3. **Stale forming after range switch (B12)**: `_formingCandles = {}` + setData
4. **`_lastCandleClose` cross-timeframe (B13)**: Null reset on range change
5. **Market-closed guard (B14)**: `window._marketOpen` check
6. **Day H/L null (B18)**: `(live.open != null && live.open > 0)`
7. **P&L initial value (B19)**: Strict null checks
8. **DRREDDY "No chart data"**: yfinance fallback added
9. **Client clock dependency (ChatGPT finding)**: `new Date(msg.ts)` in WS handler
10. **Drawing overlay sync (ChatGPT finding)**: getChartCoordinateAPI, visible range + crosshair subscriptions
11. **ResizeObserver magic numbers (ChatGPT finding)**: Replaced hardcoded 420 with `parent.offsetHeight || window._chartLastSize.height`
12. **Variable aliasing (ChatGPT finding)**: Removed duplicate `window._lastLivePrice`
13. **localStorage quota (ChatGPT finding)**: 2MB size cap on `llp` key

## Backend Endpoints Added
- `GET /api/stock-data/candle/latest` — Latest completed candle for WS reconnect recovery
- `GET /api/stock-data/intraday/paginated` — Paginated intraday with `before`/`after` timestamps
- `GET /api/stock-data/range/paginated` — Paginated daily candles with `before`/`after` dates
- `GET /api/stock-data/intraday/since` — Candles since a given timestamp

## SQL Migration
- `v002_materialized_views.sql`: Materialized views for 30m and 1h intraday aggregation with `array_agg`

## Key Decisions
- **IndexedDB** (not Cache API) for structured candle data — async, 50MB quota, native JSON
- **Forming candles stay in-memory only** — no IndexedDB writes (200ms WS cadence would thrash I/O)
- **Materialized views** (not tables) for 30m/1h — less duplication, easier refresh
- **Dynamic storage budget** via `navigator.storage.estimate()` — floor=20MB, ceiling=200MB, 20% of available
- **Memory cache** (`_recentRanges` Map) stores max 10-100 entries (dynamic based on budget)
- **TTL is market-aware** — 4x longer when market closed
- **Server timestamp** from `msg.ts` takes precedence over client clock for candle slot calc
- **All cache reads have try/catch** fallback to API — cache is non-critical

## Next Steps (for future session)
- Add lazy-load subscription in `processBigChartPrice` — detect scroll left of loaded data, call paginated fetch
- Run materialized view SQL on production DB
- Full pipeline test: cache miss → API → IDB write → cache hit → memory → render
- Monitor IndexedDB storage usage and TTL expiration in production

### Session 3: TradingView Advanced Charts Migration

**Goal:** Replace Lightweight Charts with TradingView Advanced Charts (commercial license) to natively resolve all candle/real-time bugs.

## Files Created
- `frontend/tv-datafeed.js` — Full TV Datafeed API implementation (onReady, searchSymbols, resolveSymbol, getBars, subscribeBars, unsubscribeBars, getServerTime); pipes `dashboard_price_update` CustomEvent into subscribeBars; real-time bar slot alignment in IST; NSE session info
- `frontend/tv-chart.html` — Standalone chart page loading charting_library.standalone.js + datafeed; TradingView.widget with dark theme, volume, candle overrides

## Files Modified
- `frontend/package.json` — Added `charting_library` npm dependency (private GitHub SSH) + postinstall copy script
- `frontend/dashboard.js` — Fixed `addTickers` bug: was sending `{ tickers: ... }` but backend expects `{ topics: ... }`
- `frontend/tv-datafeed.js` — Calls `DashboardWS.addTickers([symbol])` in `subscribeBars` so WS subscribes to the viewed symbol
- `backend/main.py` — Added `/api/time` (server UTC clock for getServerTime) and `/api/stock-data/search` (symbol search for searchSymbols) endpoints

## Key Decisions
- **Custom Datafeed over UDF**: UDF doesn't support streaming natively. Custom Datafeed pipes AngelOne WS directly into subscribeBars via `dashboard_price_update` CustomEvent listener.
- **charting_library copied via postinstall**: charting_library must be served as static assets (not bundled). Postinstall copies `node_modules/charting_library` to frontend root so FastAPI StaticFiles can serve.
- **KnownSymbol map + API fallback**: Hardcoded common NSE symbols (NIFTY, RELIANCE, etc.) for instant resolveSymbol. Unknown symbols fall back to `/api/live-prices`.
- **Bar time alignment**: `_getBarTime()` uses `Date.now() + _serverClockOffset` for IST-aligned bar slots. Daily bars use `Date.UTC(y,m,d)`.
- **No IndexedDB cache in datafeed**: TV has its own client-side caching. `tv-datafeed.js` fetches directly from backend API.
- **DashboardWS ticker subscription**: TV symbols are added to DashboardWS subscription list via `addTickers()` in `subscribeBars`, ensuring WS sends price ticks for the viewed symbol.
- **supported_resolutions** `['5','15','30','60','1D','1W','1M']`: TV builds '120'/'240' from '60' and '1W'/'1M' from '1D' automatically.

## Next Steps (TV migration)
1. Run `npm install` in frontend/ to pull charting_library from private GitHub repo (requires SSH key + TradingView access invitation)
2. Test `tv-chart.html?ticker=NIFTY&interval=15` — verify bar load + real-time streaming + interval switching
3. Replace Lightweight Charts on `dashboard.html` / `market.html` / `index_chart.html` with Advanced Charts widget
4. Remove legacy chart code (`lightweight-charts.js`, chart-specific sections of `dashboard.js`, `market.html`)
5. Full pipeline test: symbol search → resolve → bar load → WS streaming → interval switch → lazy-load

### Session 4: 10 aggregator bugs fixed (Claude audit)

**Goal:** Fix 10 bugs in `aggregator.py` from Claude code review — 1 critical, 3 high, 3 medium, 3 low.

### File Modified
- `backend/aggregator.py` — All 10 fixes

### Bugs Fixed
| # | Severity | Symptom | Fix |
|---|----------|---------|-----|
| 1 | 🔴 CRITICAL | `NameError: name 'flushed_1h' is not defined` on every hourly rollover — 1D volume never updated, aggregator crashes each hour boundary | `flushed_1h["volume"]` → `completed_1h["volume"]` (L594) |
| 2 | 🔴 HIGH | `_flush_batch_now()` called inside `with self._lock:` — acquires `_flush_queue_lock` while holding aggregator lock, latent deadlock | Moved `_flush_batch_now()` call outside the lock block |
| 3 | 🟠 HIGH | `recover_from_db()` hardcoded `.limit(200)` — only 200 of 794+ active tickers recover forming candle state after restart | Removed `.limit(200)`, added `filter(Candle.timestamp >= today_open)` for performance |
| 4 | 🟠 HIGH | Post-market tick (after 15:30) only flushed `["1D"]` then `del self.active_candles[ticker]` — 1m/5m/15m/30m/1h forming candles silently deleted without flush | Changed to iterate all TFs `["1m","5m","15m","30m","1h","1D","1W","1M"]` |
| 5 | 🔴 MEDIUM | `o, h, l, v = fix_ohlc(...)` — `v` is close price but naming implies volume; future copy-paste would set `volume: v` | Renamed `v` → `c_close` in both `process_tick` and `get_current` |
| 6 | 🟠 MEDIUM | `ON CONFLICT` upsert set `"open": insert.excluded.open` — unconditionally overwrites stored open (e.g. backfilled correct open replaced by later flush) | Changed to `"open": Candle.open` — preserves existing open on conflict |
| 7 | 🟠 MEDIUM | `_gc_inactive()` called on every tick inside lock — O(n) scan of all tickers at 1/sec | Added 60s rate-limit via `_last_gc` |
| 8 | 🟡 LOW | `_warned_suspicious_ts` / `_warned_frozen_ts` lazily created via `getattr`/`hasattr` in hot path — not thread-safe | Initialized in `__init__`, removed hot-path lazy init |
| 9 | 🟡 LOW | `recover_from_db()` created phantom forming candles when restarting after market close (15:30+) | Wrapped forming candle init in `if is_market_hour(now_dt):` |
| 10 | 🟡 LOW | `compute_candle_health` used `datetime.now()` (UTC on Linux) vs IST-naive candle timestamps — 5.5h offset | Changed to `ist_now_naive()` |

### Total Progress
- **18 of 14+ candle building/storage bugs fixed** (8 from earlier sessions + 10 from this audit)
- Original 14-bug tracker now has 12 fixed; 2 low-severity remain unaddressed
- Plus 8 new findings from Claude audit (bugs 2-10 except bug 1 which is residual from old BUG 5 fix)

### Session 5: Backend Startup Hang & Infinite Loading Fix

**Goal:** Fix backend startup deadlock causing dashboard on `http://localhost:8000` to hang indefinitely on the loading screen.

**Root Causes Discovered & Fixed:**
1. **Reentrant Lock Deadlock on AngelOne Tick**:
   - `angelone_service.latest_ticks_lock` was initialized as `threading.Lock()`.
   - `_handle_ws_tick` in `angelone_service.py` acquired `latest_ticks_lock` and dispatched `on_tick_callback`.
   - `_on_angel_tick` in `main.py` acquired `angelone_service.latest_ticks_lock`.
   - Non-reentrant lock resulted in an immediate self-deadlock on the first WebSocket tick during startup, freezing `_get_all_market_prices()`, baseline snapshot creation, and preventing FastAPI from ever completing startup and binding port 8000.
   - **Fix**: Replaced `threading.Lock()` with `threading.RLock()` in `backend/angelone_service.py` (L90).
2. **PostgreSQL Database URL Mismatch in News Sentiment**:
   - `News_Sentiment/.env` had outdated database credentials (`medikart@3145`), causing startup connection retries and delay.
   - **Fix**: Updated `DATABASE_URL` with correct credentials.
3. **AngelOne REST Recovery Rate Limit & Thread Explosion**:
   - `ViewedTickerManager.view` spawned unthrottled background threads on every ticker subscribed via WebSocket (~80 tickers simultaneously on dashboard load), hitting AngelOne's historical REST API and exceeding broker rate limits (`b'Access denied because of exceeding access rate'`).
   - **Fix**: Added rate-limited background queue in `backend/recovery_service.py` (`queue.Queue` with single worker + 0.35s delay) and updated `patched_view` in `backend/main.py` to enqueue requests cleanly.
4. **Playwright Async/Sync Event Loop Destruction in News Sentiment**:
   - `_refresh_news_cache` in `News_Sentiment/backend/app/api/scanx_news.py` ran `scrape_all_sync` in a thread pool executor, which created and destroyed temporary event loops holding shared Playwright instances, causing async connection aborts.
   - **Fix**: Awaited `GoogleNewsPlaywrightScraper` directly inside the async function and closed browser cleanly.
5. **Verification**:
   - `main:app` startup completes in ~12 seconds.
   - Both Stock Market (`http://127.0.0.1:8000`) and News Sentiment (`http://127.0.0.1:8003`) are running and healthy via `start_all.py`.
   - `POST /api/live-prices` (6.8ms), `GET /api/market-movers` (6.0ms), `GET /api/stock-data/range` (26ms), and `ws://127.0.0.1:8000/ws/dashboard` tested and verified streaming real-time data smoothly without rate limits.

### Session 6: Weekly/Monthly Multi-Tier Data Migration & Pagination 404 Fix

**Goal:** Fix 404 console error on `/api/stock-data/history-before` when viewing 1W/1M charts and fix historical 1W/1M multi-year data migration/gap-fill so older historical weekly candles (2-5 years) merge with recent 1D-resampled weekly candles.

**Root Causes Discovered & Fixed:**
1. **Missing `/api/stock-data/history-before` Endpoint**:
   - When users opened or scrolled backwards on 1W or 1M charts, `dashboard.js` called `/api/stock-data/history-before?ticker=...&timeframe=1W&before=...`, which did not exist on the backend and threw a 404 Not Found console error.
   - **Fix**: Added `@app.get("/api/stock-data/history-before")` in `backend/main.py` supporting `1W`, `1M`, and `1D` with epoch timestamps, returning 200 OK with paginated data.
2. **Weekly/Monthly Historical Gap-Fill Logic**:
   - In `_resample_gap_fill` (`backend/main.py`), `need_weekly_fill` only checked if the latest candle was > 14 days old (`(today_ist - last_stored_date).days > 14`).
   - If a ticker had only recent 1W candles in DB (from 2024 to 2026), `need_weekly_fill` returned `False` and never fetched the older 2-5 year history (e.g. 2022 to 2024 for SYNCOMF).
   - **Fix**: Updated `need_weekly_fill` and `need_monthly_fill` to check if earliest stored candle is missing the 5-year/10-year historical tier (`first_stored_date > five_yr_cutoff` or `len(stored_dict) < 150`). When triggered, fetches full history (`period='max'`) from yfinance/AngelOne, stores completed weeks to `candles(1W)`, and merges with 1D-resampled candles using Monday 00:00:00 IST alignment.
3. **Verification**:
   - `GET /api/stock-data/weekly?ticker=SYNCOMF` returns all 203 weekly candles from `2022-11-14` to `2026-09-28` (complete 4-year history from listing date).
   - `GET /api/stock-data/history-before?ticker=SYNCOMF&timeframe=1W&before=1700000000` returns `200 OK` with zero 404 console errors.

### Session 7: Mobile View Header Fixes & Indices Ticker Strip Card Alignment

**Goal:** Fix index card alignment inconsistencies on dashboard ticker strip when change values have 2-digit percentages/large point changes, hide "LEVERAGE" text in navbar on mobile, and refine stock page spacing.

**Root Causes & Fixes:**
1. **Ticker Strip Index Card Inconsistency & Height Jumping**:
   - In `responsive.css`, `@media (max-width: 480px)` set `.ticker-card { min-width: 110px !important; }`, which forced 2-digit index price changes (e.g. `SENSEX ▼ -242.65 (-0.33%)` or `BANK NIFTY ▼ -211.70 (-0.39%)`) to wrap into two lines while smaller numbers stayed on one line, causing uneven card heights.
   - **Fix**: Added `min-width: 155px` (desktop/tablet) and `min-width: 148px` (mobile), `min-height: 104px`, `box-sizing: border-box`, `display: flex; flex-direction: column; justify-content: space-between;` and `white-space: nowrap !important` across `index.css` and `responsive.css`.
2. **Mobile Nav Logo Text Cleanup**:
   - Hidden `.logo-text` on screens `< 768px` in `nav.css` so the brand icon cleanly fits on mobile screens without crowding the header.
3. **Stock Page Layout Alignment**:
   - Adjusted `#market-status-pill` position and reduced header row bottom margin to `0.5rem` on `stocks.html`.
4. **Soft & Eye-Friendly Expiry Badges**:
   - Replaced harsh solid neon red (`#f23645`) and bright yellow (`#f59e0b`) backgrounds on `Expiry Today` / `Expiry Tomorrow` badges with soft, translucent pill backgrounds (`rgba(242,54,69,0.14)` and `rgba(245,158,11,0.14)`), refined text colors (`#ff6b7a` and `#fbbf24`), and delicate matching borders to eliminate eye strain in dark mode.

### Session 8: Sector Performance Click-Through & showSectorTop5 ReferenceError Fix

**Goal:** Fix `Uncaught ReferenceError: showSectorTop5 is not defined` when clicking any sector in Sector Performance on `markets.html`.

**Root Causes & Fixes:**
1. **Unexported Closure Function**:
   - `showSectorTop5` in `markets.html` was declared as an internal function inside the script's IIFE `(function() { ... })()`. Inline HTML `onclick="showSectorTop5(...)"` handlers look up functions in the global `window` scope and threw `ReferenceError`.
   - **Fix**: Exported `window.showSectorTop5 = showSectorTop5;`.
2. **Sector Name Fuzzy Alias Matching**:
   - Enhanced `_sectorLeaderEntryFor` to match aliases and partial sector names (e.g. `Banking` <-> `Financial Services`, `Auto` <-> `Automobile`, `Pharma` <-> `Healthcare`, `Oil & Gas` <-> `Energy`).
3. **Interactive Sector Modal UX**:
   - Upgraded modal design with dark elevated backdrop blur, close `(x)` button, Escape key listener, and clickable stock rows routing directly to `overview.html?ticker=...`.

### Session 9: Universal Subnav Back Navigation & Portfolio UI Harmony

**Goal:** Provide a dedicated Back navigation button below the header (matching the chart page subheader style) across all non-dashboard pages, and refine portfolio mobile layout and palette.

**Root Causes & Fixes:**
1. **Universal Subnav Bar Below Header**:
   - In `nav.js`, added `.page-subnav-bar` positioned directly beneath `<header class="header">`.
   - Renders a clean `[ ← Back ]` button for all non-dashboard, non-chart pages (`Markets`, `Stocks`, `Stock Overview`, `Portfolio`, `Watchlist`, `News`, `Screener`, `Profile`, `Settings`, etc.), navigating via `window.history.back()` with fallback to `home.html`.
   - Excluded on Dashboard (`home.html` / `index.html`) and Chart pages (`stock.html`, `chart.html`, `market.html`) which feature their own chart controls bar.
2. **Portfolio UI & Mobile View Optimization**:
   - Replaced glaring neon colors on buttons, badges, and validation messages with soft pro-trader emerald (`#26a69a`) and crimson (`#ff6b7a`).
   - Preserved clean 2-column card grid and compact typography on mobile view.

### Session 10: Heatmap Sector Tile Isolation & Sector Price Synchronization

**Goal:** Fix visual bleeding/misplacement of stocks across sectors in Heatmap "By Sector" mode (e.g. banking tiles appearing next to Auto sector), and synchronize sector percentage changes between Sector Performance and Heatmap sector headings.

**Root Causes & Fixes:**
1. **Heatmap Grid Multi-Row Span Bleeding**:
   - In `markets.html`, multi-row spanning tiles (`.hm-size-xl` / `.hm-size-lg` with `grid-row: span 2`) inside a flat `.market-heatmap-grid` caused CSS grid auto-placement to wrap remaining slots around full-width headers (`.hm-sector-heading`), visually intermixing stocks from Banking into Auto and other sectors.
   - **Fix**: Wrapped each sector in an isolated container (`.hm-sector-block` and `.hm-sector-tiles`) with clean auto-fill column sizing (`grid-template-columns: repeat(auto-fill, minmax(130px, 1fr))`) so stocks strictly stay within their own designated sector block.
2. **Sector Performance & Heatmap Price Discrepancy**:
   - The Heatmap sector heading previously calculated a raw arithmetic mean of the top 3-5 stocks (`avg = stocks.reduce(...) / stocks.length`), which differed from the true Sector Index change (`BANKNIFTY`, `NIFTY_AUTO`, `NIFTY_IT`, etc.) shown in Sector Performance above.
   - **Fix**: Added `_getSectorPct()` to synchronize the exact live sector index percentage from `sectorPcts` (with fuzzy alias mapping) across both Sector Performance and Heatmap sector headers, and updated `updateSectorsFromData()` to re-render the heatmap synchronously whenever sector prices change.


### Session 11: Back-Forward Cache (bfcache) & Page Lifecycle WebSocket Fix

**Goal:** Fix browser console errors (`WebSocket connection failed: Page entered Back-Forward Cache` and `[DashWS] Error`) when navigating between pages or pressing Back/Forward buttons.

**Root Causes & Fixes:**
1. **Unmanaged WebSocket Teardown on Page Freeze / Navigation**:
   - Modern browsers freeze background pages into the Back-Forward Cache (bfcache) on navigation. Leaving an active WebSocket open caused browsers to forcibly abort the connection, triggering noisy red `onerror` console errors and scheduling rogue reconnect timeouts while frozen in cache.
   - **Fix**:
     - Added `pagehide` and `beforeunload` listeners in both `frontend/dashboard.js` and `frontend/ws.js` to gracefully close the WebSocket (`ws.close(1000)`) and clear reconnect timers prior to bfcache transition.
     - Added `pageshow` listener (`evt.persisted`) to cleanly re-establish live WebSocket streaming when the user navigates back to the page.
     - Suppressed noisy `onerror` logs when the page is navigating away, hidden, or closing cleanly.

### Session 12: Minichart Automatic Historical Backfill & Index Switching Gap-Fill Fix

**Goal:** Ensure the dashboard minichart automatically triggers complete historical and intraday backfill upon opening and whenever switching between indices (NIFTY, SENSEX, BANK NIFTY, FIN NIFTY, MIDCAP, SMALLCAP, etc.).

**Root Causes & Fixes:**
1. **Shallow Gap Detection & Missing History Backfill in `get_intraday_paginated`**:
   - Gap detection only checked tip gaps (`tip_gap_minutes > bucket_min * 2`) and internal gaps, but omitted historical depth verification. When a ticker only had recent live ticks or few candles in DB (`len(records) < 300`), gap-fill was never triggered, leaving the minichart truncated with no multi-week/multi-month history.
   - **Fix**: Added `has_history_gap` (`len(records) < 300 or (now - records[-1].timestamp).days < 14`), setting `fill_start = now - timedelta(days=60)` to automatically backfill the full 60-day historical window.
2. **Historical Bar Discard in `perform_on_demand_backfill`**:
   - For indices, `perform_on_demand_backfill` filtered downloaded Yahoo Finance candles with `backfill_start <= dt <= now`. When `backfill_start` was set to recent time, all ~4,400 earlier historical 5m bars were discarded instead of stored in PostgreSQL.
   - **Fix**: Ingested all valid candles (`dt <= now`) into `intraday_candles` and persisted to DB using `on_conflict_do_nothing(constraint="uix_candle_key")` with `is_backfilled=True` and `data_source="YFINANCE"`.
3. **Comprehensive Index & Alias Normalization**:
   - Added centralized `INDEX_ALIASES_MAP` covering all variations (`NIFTY`, `NIFTY50`, `^NSEI`, `SENSEX`, `BSESN`, `^BSESN`, `BANKNIFTY`, `NIFTYBANK`, `^NSEBANK`, `FINNIFTY`, `NIFTYFIN`, `NIFTY_FIN_SERVICE`, `MIDCAP`, `MIDCPNIFTY`, `^NSEMDCP50`, `SMALLCAP`, `NIFTYSMLCAP100`, `^CNXSC`, etc.) across `_normalize_ticker`, `_yfinance_ticker`, and `perform_on_demand_backfill`.
4. **Minichart Auto-Scaling and Cache Hygiene in `dashboard.js`**:
   - Removed `!isCached` restriction on `setVisibleLogicalRange` in `_applyNiftyChartData` so the minichart immediately fits and scales cleanly on both cached restore and live API fetch.
   - Standardized cache keys and ticker normalization across `loadChartData` and ticker select dropdown changes.

### Session 13: Universal Stock News Search & ScanX Ticker Resolution Fix

**Goal:** Fix "News Not Found" errors on specific stocks (e.g. `SYNCOMF`, `DRREDDY`, `PAYTM`, `SUZLON`) by improving Google News / ScanX query resolution, and unify ticker news search across `news.html`, the chart modal in `stock-ui.js`/`news.js`, and `overview.html`.

**Root Causes & Fixes:**
1. **Missing Company Name Resolution & Narrow Ticker Keywords**:
   - Google News and ScanX articles frequently index stocks by their registered company name or brand (e.g., "Syncom Formulations" for `SYNCOMF`, "Dr. Reddy's" for `DRREDDY`, "Paytm" for `PAYTM`) rather than strict exchange symbols.
   - `STOCK_META` only held ~50 stocks, causing `meta_name` to default to empty for over 5,000 stocks and strict keyword filtering to reject relevant news.
   - **Fix**:
     - Added dynamic fallback lookup against PostgreSQL `models.StockMetadata` and expanded `KNOWN_TICKER_NAMES` for popular tickers in `backend/main.py`.
     - Generated comprehensive search queries: `scanx.trade/{ticker}`, `scanx.trade {ticker}`, `scanx.trade {cname}`, and `scanx.trade "{cname}"`.
     - Expanded `filter_keywords` to include distinct company name tokens and primary brand words.
     - Added broader financial news RSS fallback if ScanX has no articles for a niche stock.
2. **Concurrent RSS Multi-Query with ThreadPoolExecutor**:
   - Running 4-6 queries sequentially in `_parse_rss_queries()` previously added 4-8 seconds of latency.
   - **Fix**: Implemented `ThreadPoolExecutor` to fetch and parse Google News feeds concurrently, reducing latency to < 1s.
3. **Unified Frontend Search & Category Filter Sync in `news.html`**:
   - On `frontend/news.html`, searched articles were previously not normalised through `_normaliseArticle(a, 'ScanX')`, and `allNews` was not updated on search, causing category tabs (Bullish, Bearish, Earnings, etc.) to revert to stale market news.
   - **Fix**:
     - Normalised search results through `_normaliseArticle(a, 'ScanX')` and updated `allNews = articles`.
     - Added URL parameter detection (`?ticker=...` / `?q=...`) to auto-populate the search box and trigger live queries when navigated from other pages.
     - Synchronized sentiment scores, labels, and modal presentation across `news.html`, `news.js`, and `stock-ui.js`.

### Session 14: Lightweight Charts "Value is null" Exception & Area Series Sanitization

**Goal:** Fix `Uncaught Error: Value is null` in Lightweight Charts occurring at `Oi.Area` when rendering or updating the dashboard minichart and portfolio charts.

**Root Causes & Fixes:**
1. **Unfiltered Null Values & Incomplete Bars in `_applyNiftyChartData`**:
   - `_applyNiftyChartData` in `frontend/dashboard.js` mapped `data` directly into `areaData` with `{ time: toTimeNum(p.time), value: p.close }`. When any data item had null/undefined/missing close prices, invalid timestamps (`time <= 0`), or timestamp collisions, Lightweight Charts threw `Uncaught Error: Value is null at Oi.Area`.
   - **Fix**:
     - Added strict data validation in `_applyNiftyChartData`: filtered out invalid points where `close`, `open`, `high`, `low` are null/NaN/<= 0 or `toTimeNum(p.time)` is invalid.
     - Sorted and deduplicated points by timestamp (keeping the latest valid point per bar bucket).
     - Wrapped `.setData()` calls in try/catch blocks.
2. **Missing Value Protection in `calcSMA`**:
   - `calcSMA` did not guard against missing or non-finite values, which could produce `NaN` entries in `_niftySMASeries`.
   - **Fix**: Added boundary checks, skipped missing elements, and ensured calculated averages are finite numbers before pushing to the series.
3. **Live Candle / Area Series Update Guard in `updateNiftyLiveCandle`**:
   - Guarded `_niftyCandleSeries.update()` and `_niftyAreaSeries.update()` so they are only invoked when `candleTime` and `price` are valid, positive numbers.
4. **Portfolio Area Chart Sanitization**:
   - Applied positive timestamp sorting and deduplication to the portfolio balance area chart in `frontend/portfolio.html`.

### Session 15: Order Placement Processing UI Feedback & Close Position TypeError Fix

**Goal:** Fix `TypeError: Cannot read properties of undefined (reading 'toLocaleString')` on closing positions and prevent multiple rapid order submissions by adding processing feedback and double-click submission locks.

**Root Causes & Fixes:**
1. **Missing `balance` in `/api/trade/close-position` Backend Response**:
   - `/api/trade/close-position` in `backend/main.py` returned `{"message": "Position closed", "position_id": position.id, "realized_pnl": position.realized_pnl}` without `balance` or `virtual_balance`.
   - In `frontend/stock-logic.js`, accessing `data.balance.toLocaleString()` threw `TypeError: Cannot read properties of undefined (reading 'toLocaleString')`. The `catch` block displayed this red error string directly inside the close confirmation modal even when the position had closed successfully. When the user clicked "Yes, Close" a second time, the server returned `400 Bad Request` because the position was already closed.
   - **Fix**:
     - Updated `/api/trade/close-position` in `backend/main.py` to query and return updated user `balance` and `virtual_balance`.
     - Added null safety checks in `frontend/stock-logic.js` and `frontend/portfolio.html` to guard `data.balance` before calling `toLocaleString`.
2. **Double-Click Lock & Animated Processing State on Order Placement**:
   - `submitTrade` in `frontend/stock-logic.js` lacked an in-flight guard flag and visual loading state, allowing users to submit duplicate orders with rapid clicks.
   - **Fix**:
     - Added `window._isSubmittingTrade` guard.
     - Immediately disabled the button, set `cursor: not-allowed`, `opacity: 0.7`, and rendered an inline animated SVG spinner with `Placing Order...`.
     - Guaranteed clean state restoration in a `finally` block.
3. **Double-Click Lock & Loading State on Position Close**:
   - Added `window._isClosingPosition` guard, button spinner, and disabled styling on `confirmClosePosition` across `stock-logic.js`, `stock.html`, `stock_restructured.html`, and `portfolio.html`.

### Session 16: Overview Buy/Sell Action Routing & Mobile Open Positions Responsive UI

**Goal:** Route "Buy" and "Sell" buttons on `overview.html` directly to the interactive chart page (`stock.html`) with the trading modal automatically opened and pre-selected (`LONG` for Buy, `SHORT` for Sell), and completely redesign the Open Positions / Trade History panel for mobile devices (`< 768px`) for a clean, touch-friendly, native trading experience.

**Root Causes & Fixes:**
1. **Overview Page Navigation Redirect**:
   - In `frontend/overview.html`, the "Buy" and "Sell" action buttons had hardcoded click handlers `onclick="window.location.href='portfolio.html'"`.
   - **Fix**: Replaced with dedicated event listeners navigating to `stock.html?ticker=${encodeURIComponent(ticker)}&action=buy` and `stock.html?ticker=${encodeURIComponent(ticker)}&action=sell`.
2. **Auto-Opening & Pre-Selection of Trading Panel via URL Parameter**:
   - `showTradeModal(defaultType)` and `handleTradeClick(defaultType)` in `frontend/stock-logic.js` now accept `defaultType` (`'LONG'` or `'SHORT'`).
   - Added URL parameter detection on `DOMContentLoaded` (`action=buy` / `action=sell` / `action=long` / `action=short` / `action=trade`) to immediately reveal the trading sidebar and activate the appropriate Buy (Long) or Sell (Short) mode.
3. **Mobile Open Positions & Trade History Card Redesign**:
   - On mobile devices (`< 768px`), standard 8-column HTML tables caused severe column clipping, awkward word wraps, unreadable P&L, and cut-off action buttons.
   - **Fix**:
     - Added responsive mobile card containers (`#openPositionsMobileList` and `#closedPositionsMobileList`) in `frontend/stock.html`.
     - Updated `renderOpenPositions` and `renderClosedPositions` in `frontend/stock-logic.js` to render sleek mobile cards on `< 768px` and full tables on `> 768px`.
     - Mobile cards display:
       - Top header with stock logo, bold ticker name, position type badge (`▲ LONG` / `▼ SHORT`), and prominent color-coded P&L badge (`+₹...` / `-₹...`).
       - Clean 3-column metrics grid: Quantity, Entry Price, CMP, and Target (TP) / Stop Loss (SL).
       - Prominent, touch-friendly `[ ✏️ Edit TP / SL ]` and `[ ✕ Close Position ]` action buttons.
     - Fixed `--` / `—` ticker placeholders with fallback to `window.currentTicker`.

### Session 17: Mobile Open Positions Panel Height & Card Compactness Optimization

**Goal:** Shrink the Open Positions / Trade History bottom drawer on mobile phones so the chart retains > 75% of screen height, eliminating vertical cramping and making position cards sleek and compact.

**Root Causes & Fixes:**
1. **Excessive Panel Viewport Height**:
   - `#openPositionsPanel` was configured with `max-height: 48vh !important; min-height: 180px !important;` on mobile, which occupied half the screen and squeezed the chart into a small box.
   - **Fix**: Reduced to `max-height: 24vh !important; min-height: 100px !important;` in `stock.html` so the chart keeps 75-80% of the mobile screen.
2. **Tab Header Wrapping & Height Squander**:
   - The top tab bar wrapped onto two separate lines on mobile due to `flex-wrap: wrap` and large padding.
   - **Fix**: Set `flex-wrap: nowrap !important; margin-bottom: 4px !important; gap: 4px !important;` and reduced button heights to `22px` with compact text (`0.75rem`), fitting `[📊 Positions (4)]`, `[📜 History (17)]`, and `[✕ Close]` cleanly on a single slim row.
3. **Card Vertical Height Optimization**:
   - Individual position cards had multi-row stats, oversized logos, and large padding, resulting in ~180px card heights.
   - **Fix**:
     - Redesigned `.pos-mobile-card` into an ultra-compact ~68px layout (over 60% height reduction).
     - Inline header: 18px logo + bold ticker + type pill (`▲ LONG`) + right-aligned P&L badge (`+₹...` / `-₹...`).
     - 4-column metric strip: Qty, Entry, CMP (LTP), and compact TP / SL.
     - Slim action buttons: `22px` height `[ ✏️ Edit TP/SL ]` and `[ ✕ Close ]`.




### Session 18: Screener Mobile Sector Dropdown & Custom Select Optimization

**Goal:** Fix the oversized Sector (and Volume Ratio) dropdown in Screener settings/filters on mobile view (screener.html), preventing viewport overflow and ensuring a compact, scrollable dropdown container.

**Root Causes & Fixes:**
1. **Oversized Native Dropdown on Mobile Webview**:
   - The native <select> element rendered unconstrained popup dimensions on mobile devices (~400px+ wide), overflowing beyond the right edge of the screen and stretching vertically across the screen.
   - **Fix**:
     - Built custom, responsive select dropdown components (.custom-select-wrapper, .custom-select-trigger, .custom-select-menu, .custom-select-item) for Sector and Volume Ratio filters in screener.html.
     - Strictly bound dropdown width to 100% of the mobile drawer with ox-sizing: border-box; and constrained menu height to max-height: 200px with custom slim dark scrollbars.
2. **Interactive Dropdown Selection & State Synchronization**:
   - Implemented 	oggleCustomSelect(wrapperId), selectCustomOption(selectId, value, labelText), and updateCustomSelectUI(...) helper functions.
   - Updated initSectors(), syncToMobile(), syncFromMobile(), dynamic backend sector discovery, unPreset(), clearAll(), and clearChip() to seamlessly synchronize active item states and label text across mobile and desktop.
   - Added outside-click dismissal to cleanly close open menus.

### Session 19: Screener Multi-Sector Alias Resolution & Word-Boundary Accuracy

**Goal:** Fix empty screener results when selecting Technology or Oil & Gas in the screener filters, and eliminate cross-sector false positive matches.

**Root Causes & Fixes:**
1. **Missing Sector Alias Resolution**:
   - In PostgreSQL metadata (models.StockMetadata) and sector_data.json, technology stocks were classified under 'IT' and oil/gas stocks under 'Energy'.
   - When a user selected 'Technology' or 'Oil & Gas', raw SQL queries sector ILIKE '%Technology%' and sector ILIKE '%Oil & Gas%' failed to match the canonical stored names, returning 0 results.
   - **Fix**:
     - Added comprehensive SECTOR_ALIASES_MAP covering all sector variations and synonyms (e.g., 'Technology'/'Tech' <-> 'IT', 'Oil & Gas'/'Oil and Gas' <-> 'Energy', 'Healthcare' <-> 'Pharma').
     - Added _get_sector_aliases() to dynamically resolve any requested sector into its canonical database equivalents.
2. **Short Word Substring Collision Protection**:
   - Two-letter sector codes like 'IT' previously collided with substring checks against 'Equities', 'Utilities', and 'Capital', causing false positive leaks across sectors.
   - **Fix**:
     - Implemented _is_sector_match() with regex word-boundary matching (\bIT\b) for codes <= 3 characters.
     - Structured SQLAlchemy filtering to use exact equality and word-boundary conditions for short abbreviations.
3. **Verification**:
   - Verified live API queries for all 14 sectors via GET /api/screener?sector=...:
     - Technology: 24 stocks (TCS, INFY, LTTS, MPHASIS, SONATSOFTW, PERSISTENT, etc.)
     - Oil & Gas: 23 stocks (RELIANCE, ONGC, BPCL, IOC, PETRONET, SUZLON, etc.)
     - Banking: 33 stocks, Auto: 31 stocks, Pharma: 26 stocks, Industrials: 31 stocks, Consumer Durables: 19 stocks, Metal: 14 stocks, FMCG: 17 stocks, Telecom: 6 stocks, Media: 6 stocks.

### Session 20: Mobile News Page Layout Fix

**Goal:** Fix news page (news.html) mobile view � layout was broken with a vertical sidebar column above the content, inputs overflowing, and oversized typography.

**File Modified:**
- `frontend/news.html` � Replaced single-line `@media(max-width:768px)` with comprehensive mobile-only styles. Desktop styles unchanged.

**Changes Made:**
1. **Sidebar ? horizontal scrollable tab strip**: `.sidebar` to `flex-direction:row; overflow-x:auto;`. Each `.sidebar-btn` rendered as a pill chip with `white-space:nowrap; flex-shrink:0; border-radius:20px`.
2. **Search inputs stack vertically**: `.search-news` switches to `flex-direction:column`. Both inputs get `width:100%; box-sizing:border-box`. `#stockFilter` gets `max-width:100% !important`.
3. **Page title/subtitle compact**: `font-size:1.2rem / 0.8rem`, reduced margins.
4. **Featured headline compact**: `padding:1rem; border-radius:10px; h2 font-size:1.05rem`.
5. **News cards**: `padding:0.9rem`, title `0.9rem`, excerpt `0.8rem`, meta `flex-wrap:wrap; 0.72rem`.
6. **News list gap reduced**: `gap:0.6rem`.

### Session 21: Watchlist List View Redesign, Exchange Badge Detection, Auth Card Close Button, and News Card Clean-up

**Goal:**
1. Redesign Watchlist from tall grid cards into a compact, responsive list-row layout with clickable rows opening overview.html, accurate exchange badges (NSE/BSE/INDEX), and clean toast notifications.
2. Fix Auth close button on login.html and register.html to sit inside the top-right corner of the card with matching dark background and navigate back to previous referrer/page.
3. Remove 'ScanX' source badges from news cards on both Dashboard (index.html via news.js) and News Page (news.html) while keeping backend pipeline untouched.
4. Significantly increase the search bar width on news.html mobile view to 100% full width with comfortable touch target heights.

**Files Modified:**
- rontend/watchlist.html - List-row styling, removed 'flex:1' empty middle gap, dynamic exchange lookup from ALL_STOCKS, innerHTML toast notification fix.
- rontend/overview.html - Dynamic exchange badge & sector enrichment from ALL_STOCKS.
- rontend/auth.css - Positioned close button inside card corner with matching #0a0a0a background.
- rontend/login.html & rontend/register.html - Moved button inside card and improved handleAuthClose referrer navigation.
- rontend/news.js - Removed 'ScanX' label from dashboard cards and ticker modal cards.
- rontend/news.html - Removed 'ScanX' source badge from news cards and modal; expanded search bar width to 100% on mobile.

---

## Session Summary: News Card View & Search Bar Improvements
- **Goal**:
  1. Remove the "ScanX" label from the news card view on both the dashboard (index.html via 
ews.js) and the dedicated news page (
ews.html).
  2. Increase the search bar width on the news page for mobile view while keeping desktop clean and responsive.
  3. Ensure all underlying news scraping from scanx.trade remains 100% intact (view-only changes).
- **Changes**:
  - rontend/news.js: Removed the <span style="color:#4A90E2;font-weight:600;">ScanX</span> badge from enderDashboardNewsCards(), enderNewsCards(), and openNewsDetail(). News items now display clean timestamp and ticker metadata.
  - rontend/news.html:
    - Removed the <span class="source-badge">ScanX</span> badge from news cards in enderList() and from the news detail popup.
    - Updated CSS for .search-news on desktop (height: 42px; width: 100%) and mobile (@media (max-width: 768px): width: 100% !important; min-width: 100% !important; height: 44px; font-size: 0.92rem;).

---

## Session Summary: Dashboard News Fix & Search Enhancements
- **Goal**:
  1. Fix issue where news was not rendering on dashboard (index.html / home.html).
  2. Fix search race condition (e.g. searching 'SBI' and quickly typing 'SBIN' where old in-flight requests overwrote newer search results).
  3. Significantly increase search bar size, height, and touch-target padding on the news page for mobile view.
  4. Clarify backend news search mechanism from scanx.trade.
- **Changes**:
  - rontend/news.js:
    - Fixed undefined 	imeAgo variable in enderDashboardNewsCards() which caused a JS runtime error during card mapping.
    - Improved boot detection in 
ews.js to run on both DOMContentLoaded and when DOM is already interactive / complete.
  - rontend/news.html:
    - Implemented request sequence IDs (currentSearchId) and AbortSignal propagation in ilterNews() and etchNewsAPI(). Outdated in-flight requests (e.g. SBI) are now properly aborted/discarded when the user types a new query (e.g. SBIN).
    - Redesigned search bar with .search-input-wrap, integrated magnifying search icon SVG, clear button ?, and touch-friendly mobile input (52px height, 1rem font size, 100% width).

---

## Session Summary: Directory Cleanup
- **Goal**: Safely delete the redundant nested News_Sentiment duplicate folder without affecting any running components.
- **Action**:
  - Removed C:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\News_Sentiment.
  - Confirmed active News_Sentiment at C:\Users\sohan\Desktop\StockMarket\StockMarket\News_Sentiment remains untouched.
  - Verified Port 8000 and Port 8003 servers are running normally and responding with HTTP 200.

---

## Session Summary: Cleaned Up Redundant & Unnecessary Files/Folders
- **Goal**: Safely delete all redundant, duplicate, and temporary folders/files identified across the workspace without affecting active services.
- **Removed Items**:
  1. StockMarket\StockMarket\StockMarket\News_Sentiment (Redundant duplicate news backend folder)
  2. StockMarket\StockMarket\venv (Unused 1.80 GB outer virtual environment)
  3. StockMarket\StockMarket\StockMarket\nginx.conf;C (Accidental typo folder)
  4. StockMarket\StockMarket\StockMarket\tests (Empty directory)
  5. StockMarket\logs, StockMarket\StockMarket\logs, StockMarket\StockMarket\StockMarket\logs, and root 
ews_*.log files
  6. All stale .pytest_cache and __pycache__ directories
  7. Duplicate root scripts (dd_holiday.py, un.py, etc.) whose active files are maintained in StockMarket\StockMarket\StockMarket\
- **Health Verification**:
  - Port 8000 (/api/all-stocks & /api/news/search/market) -> 200 OK
  - Port 8003 (/docs) -> 200 OK
  - Active venv (StockMarket\StockMarket\StockMarket\venv) and code intact.

---

## Session 22: Market Movers Stale-Tick Fix (live dashboard)

**Goal:** Fix Top Gainers/Losers showing no price movement and values that did not match Angel One.

**Root cause (verified against live prod, read-only + diagnostic endpoint):**
1. `/api/market-movers` (`_get_all_market_prices`) overlaid `angelone_service.latest_ticks` with **no freshness gate**, while `/api/live-prices` (`fetch_batch_live_data`) applied a 900s `_received_ts` gate — so the two endpoints disagreed for the same ticker.
2. `_on_angel_tick` **overwrote the broker exchange timestamp** (`_ts`) with server time, so a *replayed stale snapshot* (broker WS sends an old quote for some illiquid tokens, e.g. CUBEXTUB's 2026-09-28 close 167.46 / prev 152.24, exchange ts 06:55 IST) looked "fresh" and was trusted.
3. Those stale quotes produced fake +10% movers that never changed intraday; the DB baseline (correct last close) was ignored. A diagnostic confirmed: `baseline CUBEXTUB = 165.27/157.4`, `tick = 167.46/152.24` (`_source=angel_ws`, fresh `_received_ts`).
4. Aggregator also coerced old timestamps to "now", writing bogus current-session candles from stale prices (e.g. KLL 5m 10:25 = 46.0).

**Fix (commit `bec24c7`, deployed via git pull → cp -ru → systemctl restart):**
- `_on_angel_tick`: preserve broker timestamp as `_exch_ts`.
- New `_tick_is_current_session(tick_data, market_open)`: rejects a quote whose `_exch_ts` is a previous day, or same-day pre-open (<09:15) while the market is open.
- `_get_all_market_prices`: skip stale ticks; mark live entries `_live` + `timestamp`.
- `fetch_batch_live_data`: same session gate → `/api/live-prices` consistent with movers.
- `_build_movers`: while market is open, list only current-session (`_live`) quotes (falls back to baseline if the live feed is fully down); when closed, baseline = last session's movers.
- Added `backend/tests/test_movers_stale_tick_gate.py` (11 tests).

**Verification (prod):** movers now tick live (RSYSTEMS 275→279, CYIENT 1155→1157, TCS 2194→2198); all 9 previously-frozen tickers (CUBEXTUB/RETAIL/KLL/QUINT/ABMINTLLTD/RADHIKAJWE/PPAP/GATECH/DCG) gone from movers; `/api/live-prices` returns `src=db` for them. 11/11 new tests + 28 existing tests pass.

**Remaining / follow-ups (NOT done):**
- Aggregator still ingests stale quotes (coerces old ts to now) → occasional bogus candles; needs a careful guard (reuse `_tick_is_current_session` in `_on_angel_tick`/`process_tick`), ideally alongside the deferred candle/volume repair.
- DB has corrupt BACKFILL 1D rows (volume 0, e.g. PPAP 2026-10-09 = 338.02 = +36.8%) and a 07:43 daily job that wrote today's rows from stale quotes.
- Prod is a 1 GB Lightsail instance; a service restart under startup load caused a gunicorn WORKER TIMEOUT + SIGKILL (OOM) and a ~30s `statement_timeout` on the baseline query, then recovered. Consider more RAM / a cheaper baseline query.
