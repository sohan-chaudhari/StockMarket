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

### Session 6: TV Drawing Tools — Engine Architecture (Phase 2) + Basic Line Tools (Phase 3.1)

**Goal:** Build a TradingView-compatible drawing engine with proper separation of concerns and implement all basic line tools.

## Files Modified
- `frontend/drawing-core.js` — DrawingEngine, HitTestService, GeometryUtils, SnappingEngine, SelectionManager, UndoRedoManager, CoordinateMapper, DrawingModel, Serializer
- `frontend/drawings.js` — All drawing renderer classes + ToolManager

## Architecture (Phase 2)
### DrawingModel (§1)
- Pure data container: id, type, points[{time, price}], style, locked, hidden, zIndex, groupId
- `toJSON()` serialization / `fromJSON()` reconstruction
- `clone()` deep copy with new id
- `equals()` structural comparison (used by UndoRedoManager)

### DrawingEngine (§2)
- Central orchestrator: `addDrawing()`, `removeDrawing()`, `updateDrawing()`, `finalizeDrawing()`
- `finalizeDrawing()` creates DrawingModel from renderer, links bidirectionally, creates snapshot, emits events, pushes undo
- `syncModel()` / `syncRenderer()` bi-directional state sync
- `render(ctx, chartState, selectedId, hoveredId)` — DPR-scaled, z-ordered, viewport-culled, with post-render handle pass
- `_getVisibleDrawings()` viewport culling using line-canvas intersection
- EventBus for decoupled event handling

### HitTestService (§3)
- 5-tier hit testing: anchor → midpoint → edge → fill → body
- Handle hit via `getAnchorPoints()` / `getMidpoints()` / `getFillShape()`
- Single-point drawing body hit via distance check

### GeometryUtils (§3.2)
- `lineCanvasIntersection()` — clip line to canvas rect
- `pointToLineSegment()` — closest point + distance
- `pointInPolygon()` — ray casting
- `segmentIntersection()` — line segment intersection
- `distance()` — Euclidean

### SnappingEngine (§3.3)
- `snapToPrice()` / `snapToTime()` / `getSnappedPos()` with configurable thresholds
- OHLC snap points from data provider
- Shift temporarily inverts snap mode
- `toggleMagnet()` / `isEnabled()`

### SelectionManager (§4)
- Single-selection via `select(id)`, `deselect()`, `getPrimary()`
- Multi-select via `toggle(id)`, `selectRange(ids)`, `clear()`
- Marquee selection support

### UndoRedoManager (§5)
- Snapshot-based undo/redo with configurable capacity (default 50)
- `capture(snapshot)`, `undo()`, `redo()`, `clear()`
- Snapshots store full drawing state array

### CoordinateMapper (Phase 1 bridge)
- `pixelToCoord(x, y)` / `coordToPixel(c)` — bridges TV chart API coordinate system
- Handles time-to-x and price-to-y conversions via TV chart methods

## P0 Fixes (Phase 2 verification)
1. **Fill hit-testing**: Unified API via `getFillShape(pixels)`. Rectangle returns 4 corners. Circle returns 32-gon. TriangleShape returns 3 vertices. ParallelChannel returns 4 vertices.
2. **Midpoint drag**: Now translates entire drawing (body drag behavior) by computing delta from midpoint start/end and mapping through `translate(dx, dy, chartState)`.
3. **Handle rendering layer**: Removed inline handle drawing from `TwoPointDrawing.draw()`. Engine's post-render pass handles it via `drawHandles()`.
4. **HiDPI**: DPR scaling via `ctx.scale(dpr, dpr)` in render pipeline. `clearRect` uses CSS coordinates.
5. **Selection model**: Single source of truth via `engine.selection.getPrimary()`. `selectedDrawing` is a getter that calls this.
6. **Snapping fully wired**: SnappingEngine in ToolManager, data provider for OHLC, `getMousePos` uses snap, Shift toggles temporarily, `toggleMagnet()` functional.
7. **Placement preview**: Already implemented — `onMouseMove` calls `currentDrawing.update()`.

## Phase 3.1 — Basic Line Tools (complete)
Refactored four 1-point tools from `TwoPointDrawing` to `BaseDrawing`:
- **HorizontalLine** — 1-point price-only line spanning full width. Constrained Y-only translate. Price badge at right edge.
- **VerticalLine** — 1-point time-only line spanning full height. Constrained X-only translate. Price badge at top.
- **HorizontalRay** — 1-point (time+price anchor), extends rightward to canvas edge. Points changed from 2→1 in ToolDefinitions.
- **CrossLine** — 1-point crosshair (vertical + horizontal through anchor). Price badge at right edge. Points changed from 2→1 in ToolDefinitions.

Moved `getPriceLabel()` and `drawPriceBadge()` from `TwoPointDrawing` to `BaseDrawing` for reuse.

Existing 2-point tools verified correct:
- **TrendLine** — 2-point, bidirectional segment
- **Ray** — 2-point (anchor + direction), extends infinitely from p1 through p2
- **ExtendedLine** — 2-point, extends infinitely both directions
- **InfoLine** — 2-point, info bubble at midpoint
- **TrendAngle** — 2-point, angle label at p2

All nine Phase 3.1 tools implement `drawHandles()` for engine post-render pass, `getAnchorPoints()`, `getMidpoints()`, and proper `translate()` behavior.

## Next Steps
- Phase 3.2: Channels — ParallelChannel (3-point), Regression Trend, Pitchfork (3-point with median + outer lines)

### Session 5: Dashboard load performance fixes

**Goal:** Fix ~1-minute dashboard load time by eliminating redundant API calls and adding fetch timeouts.

### Root Cause
On DOMContentLoaded, both `index.js` and `dashboard.js` independently call `/api/live-prices` with overlapping tickers. If AngelOne WS data is missing for any ticker, both calls fall through to yfinance (12s each via semaphore). With multiple concurrent backend consumers, each `/api/live-prices` call can take 12-24s, and 2-3 such calls = ~1 minute.

### Files Modified
- `frontend/index.js` — Skip immediate `updatePrices()` call on dashboard pages (`.dashboard-main` present); `fetchIndexPrices()` + WS events provide initial data, 15s interval still covers backup
- `frontend/dashboard.js` — Added 12s AbortController timeout to `fetchIndexPrices()`, 10s timeout to `fetchMarketMovers()` so hung requests don't delay rendering

### Key Decisions
- **Frontend-only fixes**: Backend was reverted by user earlier in this session
- **Timeout over complex caching**: AbortController is simpler and more robust than a shared promise cache across files
- **Non-dashboard pages (profile.html) still get immediate `updatePrices()`** since they don't load `dashboard.js`

### Total Progress
- **18 of 14+ candle building/storage bugs fixed** (8 from earlier sessions + 10 from this audit)
- Original 14-bug tracker now has 12 fixed; 2 low-severity remain unaddressed
- Plus 8 new findings from Claude audit (bugs 2-10 except bug 1 which is residual from old BUG 5 fix)
