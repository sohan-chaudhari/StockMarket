# FACTSHEET: LEVERAGE Stock-Market Intelligence & Real-Time Trading Platform

## 1. Platform Identification & Purpose
* **Platform Name:** LEVERAGE
* **Domain / Scope:** Indian Equity Markets (National Stock Exchange — NSE & Bombay Stock Exchange — BSE).
* **Primary Mission:** High-performance stock market analytics, algorithmic multi-timeframe candle streaming, advanced charting (TradingView & Lightweight Charts), technical indicator computation, market scanning/screener, news sentiment analysis, and simulated paper-trading execution with automated Take Profit (TP) / Stop Loss (SL) risk management.
* **Target Sizing:** 1 GB RAM, Single Gunicorn/Uvicorn ASGI Worker, Linux (AWS Lightsail / EC2 Ubuntu 24.04 LTS).

---

## 2. Technology Stack & Exact Versions
*Source: `backend/requirements.txt`, `backend/Dockerfile`, runtime environment.*

| Component | Technology | Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Language Runtime** | Python (CPython) | `3.13.7` | Backend execution engine & asynchronous API server |
| **Web Framework** | FastAPI | `0.133.0` | Asynchronous REST API routing & WebSocket handlers |
| **ASGI Toolkit** | Starlette | `1.3.1` | Core HTTP/WebSocket request processing & middleware |
| **ASGI Server** | Uvicorn (`uvicorn[standard]`) | `0.34.0` | High-performance ASGI worker engine |
| **WSGI Master** | Gunicorn | `26.2.0` | Process master & worker supervisor (`-w 1`) |
| **ORM / SQL Layer** | SQLAlchemy | `2.0.45` | Database abstraction, relational mapping & connection pooling |
| **Database Migrations**| Alembic | `1.18.4` | Version-controlled database schema migration engine |
| **Database Driver** | psycopg2-binary | `2.9.11` | PostgreSQL database adapter linked against `libpq5` |
| **Database Engine** | PostgreSQL / TimescaleDB | `15+ / 16` | Relational store for candles, metadata, users & trades |
| **Data Validation** | Pydantic | `2.10.6` | Request/Response schema validation & serialization |
| **Broker Integration** | smartapi-python | `1.3.5` | Angel One SmartAPI REST & WebSocket V2 client |
| **Market Data Fallback**| yfinance | `0.2.50` | Secondary historical candle & gap-recovery provider |
| **Password Hashing** | bcrypt | `5.0.0` | Salted cryptographic password hashing |
| **JWT Tokens** | python-jose | `3.5.0` | RFC 7519 HS256 access token creation and validation |
| **Rate Limiting** | slowapi | `0.1.9` | In-memory token-bucket API rate limiter |
| **Error Monitoring** | sentry-sdk | `2.63.0` | Error tracking (`send_default_pii=False`) |
| **HTTP Clients** | httpx (`0.28.1`), requests (`2.31.0`), aiohttp (`3.13.2`) | External API & RSS scraping |
| **Reverse Proxy** | Nginx | `1.27-alpine` / `1.24+` | TLS termination, WebSocket reverse proxy, security headers |
| **Frontend UI** | Native ES6, Canvas, TradingView Advanced Charts | Multi-page application with client-side IndexedDB caching |

---

## 3. Directory Map & Repository Structure

```text
StockMarket/
├── DEPLOYMENT_GUIDE.md                # Reference deployment guide
├── deployment_guide.md                # Production AWS deployment guide
├── docker-compose.yml                 # Docker Compose specification (app + nginx)
├── nginx.conf                         # Production Nginx reverse proxy configuration
├── AGENTS.md                          # Engineering session logs & hardening history
├── backend/
│   ├── Dockerfile                     # Production container spec (Python 3.13-slim, MALLOC_ARENA_MAX=2)
│   ├── requirements.txt               # Pinned production Python dependencies
│   ├── gunicorn.conf.py               # Gunicorn worker configuration (workers=1 hard-pinned)
│   ├── database.py                    # Database connection, engine pooling & IST helpers
│   ├── models.py                      # SQLAlchemy ORM models (Candle, User, Position, Order, etc.)
│   ├── schemas.py                     # Pydantic validation & response schemas
│   ├── auth.py                        # JWT handling, password validation, lockout & audit logging
│   ├── main.py                        # FastAPI entry point, background loops, API routes, WS managers
│   ├── aggregator.py                  # Real-time multi-timeframe candle aggregation engine
│   ├── chart_service.py               # Optimized chart retrieval, exact stored checks & live overlay
│   ├── candle_cache.py                # L1/L2 multi-tier LRU candle cache (MAX_L2_ENTRIES=5000)
│   ├── live_timeframe_manager.py      # Real-time higher-timeframe forming builders (MAX_BUILDERS=5000)
│   ├── price_poller.py                # REST background fallback price poller
│   ├── price_provider.py              # Tiered multi-source price resolution service
│   ├── recovery_service.py            # On-demand gap recovery service with rate limits
│   ├── retention_service.py           # Multi-tier historical candle retention & cleanup
│   ├── trade_service.py               # Position opening, TP/SL modification, order closing
│   ├── execution_engine.py            # Automated 2s background TP/SL execution loop
│   ├── indicator_service.py           # Technical indicator math (RSI, MACD, BB, Supertrend, etc.)
│   ├── exchange_calendar.py           # NSE trading hours & holiday validation engine
│   ├── validation_service.py          # Candle integrity, OHLC sanity & gap validation suite
│   ├── websocket_manager.py           # Client WebSocket connection managers (Dashboard & User WS)
│   ├── yfinance_downloader.py         # Throttled Yahoo Finance batch downloader
│   ├── alembic/                       # Alembic migration environment & versions (001, 002, 003)
│   ├── routers/                       # Sub-routers (auth_router.py, trade_router.py)
│   └── tests/                         # Automated test suite (1,596 test cases)
└── frontend/
    ├── index.html                     # Main market dashboard & indices ticker strip
    ├── stock.html                     # Main interactive stock chart, order panel & positions drawer
    ├── screener.html                  # Multi-factor stock screener & sector filtering
    ├── news.html                      # Financial newsfeed & sentiment scoring
    ├── portfolio.html                 # Portfolio overview, P&L analytics, position history
    ├── markets.html                   # Market heatmap, sector performance & top movers
    ├── tv-chart.html                  # TradingView Advanced Charts standalone integration
    ├── tv-datafeed.js                 # Custom TradingView JS API Datafeed implementation
    ├── dashboard.js                   # Dashboard state, WS message routing, ticker updates
    ├── stock-logic.js                 # Stock chart coordination, trading execution UI, mobile cards
    ├── cache.js                       # Client-side IndexedDB caching layer with TTL
    └── drawings.js / drawing-core.js  # Interactive technical analysis drawing tools
```

---

## 4. Execution Entry Points & Runtime Processes

* **FastAPI Application:** [`backend/main.py: app`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/main.py#L427)
* **ASGI Server Entry:** [`backend/gunicorn.conf.py`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/gunicorn.conf.py) → `gunicorn main:app -c gunicorn.conf.py` (`workers = 1`, `worker_class = "uvicorn.workers.UvicornWorker"`).
* **Docker Containerization:** [`backend/Dockerfile`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/Dockerfile) (runs as non-root `appuser`, `MALLOC_ARENA_MAX=2`).
* **Reverse Proxy:** [`nginx.conf`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/nginx.conf) (Port 80/443, proxying to `app:8000`).

---

## 5. Background Tasks & Daemon Threads

1. **Angel One WebSocket Feed:** Background thread managed by `SmartWebSocketV2`, streaming live ticks to `_on_angel_tick` (`main.py:7216`).
2. **Candle Aggregator Worker:** Background processing worker consuming tick queues and computing forming/completed multi-timeframe candles (`aggregator.py:100`).
3. **Execution Engine Loop:** Async loop in `start_execution_engine()` polling PENDING TP/SL orders every 2.0 seconds during market hours (`execution_engine.py:30`).
4. **Live Timeframe Sweep:** Background daemon thread in `LiveTimeframeManager` sweeping idle builders every 15 seconds (`live_timeframe_manager.py:270`).
5. **Candle Cache Sweep:** Background daemon thread in `CandleCache` cleaning expired L2 cache entries every 60 seconds (`candle_cache.py:126`).
6. **Recovery Service Worker:** Background daemon thread processing ticker gap-fill queue (`recovery_service.py:45`).
7. **Daily Prefill Scheduler (`_daily_prefill_all`):** Background task firing at 19:00 IST daily (and +30s after startup), fetching 1D candles for 5,543 stocks in 50-ticker batches (`main.py:7880`).
8. **Retention Cleanup Scheduler (`_retention_loop`):** Background task firing at 02:00 IST daily, pruning expired intraday candles (`main.py:7730`).
9. **Event Loop Lag Monitor:** Async probe task recording event loop delay every 100ms (`main.py:8080`).

---

## 6. Complete API Routes Inventory

### Authentication (`/api/auth`)
* `POST /api/auth/register` — Create account with password validation & email verification token.
* `POST /api/auth/login` — Authenticate user, verify lockout counter, return HS256 JWT access token.
* `POST /api/auth/logout` — Blacklist current JWT `jti` in `token_blacklist` table.
* `GET /api/auth/me` — Return current authenticated user profile.
* `POST /api/auth/forgot-password` — Issue password reset token / email.
* `POST /api/auth/reset-password` — Update password with valid cryptographic token.
* `POST /api/auth/verify-email` — Verify email address via token or OTP.
* `POST /api/auth/resend-verification` — Resend verification email.

### Market Data & Charting (`/api/stock-data`, `/api/*`)
* `GET /api/all-stocks` — Pre-rendered UTF-8 byte stream (~1.34 MB, 60s TTL) of all 5,543 stocks.
* `GET /api/stocks/version` — Daily cache invalidation version string.
* `POST /api/live-prices` — Batch resolution of live market prices and daily change.
* `GET /api/stock-data/chart` — Exact stored candle fetch with real-time forming candle overlay.
* `GET /api/stock-data/intraday/paginated` — Historical intraday candles with auto gap-recovery.
* `GET /api/stock-data/range/paginated` — Historical daily candles with date bounds.
* `GET /api/stock-data/candle/latest` — Latest completed bar for WebSocket reconnect catch-up.
* `GET /api/stock-data/intraday/since` — Intraday candles since epoch timestamp.
* `GET /api/market-movers` — Gainers, losers, and most active stocks filtered by market cap.
* `GET /api/watchlist` — Pre-calculated watchlist tickers with live price stats.
* `GET /api/screener` — Multi-factor stock filter (price, change, volume, sector alias resolution).
* `GET /api/sectors` — Sector breakdown & aggregate performance.
* `GET /api/time` — Server UTC and IST timestamps for chart synchronization.
* `GET /logos/{filename}` — Stock logo image / dynamic SVG avatar fallback generator.

### Technical Indicators (`/api/indicators`)
* `GET /api/indicators/{ticker}` — Compute RSI, MACD, Bollinger Bands, ATR, Supertrend, Stochastic for specified timeframe.

### Paper Trading (`/api/trade`)
* `POST /api/trade/order` — Place simulated Market or Limit order (Long/Short) with TP/SL.
* `GET /api/trade/positions` — List user's active OPEN and CLOSED positions.
* `POST /api/trade/positions/{id}/close` — Manually close an open position at CMP.
* `PUT /api/trade/positions/{id}/tpsl` — Update Take Profit and Stop Loss levels (max 3 edits).
* `GET /api/trade/history` — Paginated trade execution history.
* `GET /api/trade/summary` — Portfolio balance, total invested capital, realized & unrealized P&L, win rate.

### System Health & Diagnostics (`/api/health`, `/api/monitoring`)
* `GET /api/health` — Comprehensive system health report (event loop lag, DB pool, memory, task states).
* `GET /api/monitoring/candle-system` — Component health matrix (aggregator, recovery, cache, timeframe manager).
* `GET /api/validation/run` — On-demand data integrity validation check for specific ticker/timeframe.

### WebSockets (`/ws/*`)
* `WS /ws/dashboard` — High-throughput real-time tick feed with client symbol subscription (`view_ticker`, `add_tickers`).
* `WS /ws/user` — Authenticated private WebSocket broadcasting live order fills and TP/SL execution notifications.

---

## 7. Database Architecture & Schema Inventory

*Active Engine:* PostgreSQL 15+ / 16 (TimescaleDB compatible). Managed by Alembic migrations (`backend/alembic/versions/`).

### Active Tables

1. **`stock_metadata`**
   * Columns: `id` (PK), `ticker` (VARCHAR, Index), `name` (VARCHAR), `exchange` (VARCHAR), `logo` (VARCHAR), `base_price` (FLOAT), `is_active` (BOOLEAN, NOT NULL), `is_premium` (BOOLEAN, NOT NULL), `sector` (VARCHAR).
   * Constraints: `UniqueConstraint('ticker', 'exchange', name='uix_ticker_exchange')`.
   * Indexes: `ix_metadata_is_premium` (Partial index where `is_premium == True`).

2. **`candles`** (Unified Multi-Timeframe Store)
   * Columns: `id` (BigInt/Int PK), `ticker` (VARCHAR, Index), `timeframe` (VARCHAR(5), NOT NULL), `timestamp` (TIMESTAMP, Index), `open` (FLOAT), `high` (FLOAT), `low` (FLOAT), `close` (FLOAT), `volume` (BIGINT), `is_completed` (BOOLEAN), `data_source` (VARCHAR(10)), `is_backfilled` (BOOLEAN), `created_at` (TIMESTAMP).
   * Constraints: `UniqueConstraint('ticker', 'timeframe', 'timestamp', name='uix_candle_key')`.
   * Indexes: `ix_candle_ticker_tf_ts` (`(ticker, timeframe, timestamp DESC)`).

3. **`users`**
   * Columns: `user_id` (PK), `email` (VARCHAR(255), Unique, Index), `password_hash` (VARCHAR(255)), `full_name` (VARCHAR(255)), `virtual_balance` (FLOAT, Default 100000.0), `is_verified` (BOOLEAN), `is_active` (BOOLEAN), `created_at` (TIMESTAMP), `last_login` (TIMESTAMP), `failed_login_attempts` (INT), `locked_until` (TIMESTAMP).

4. **`positions`** (Paper Trading Positions)
   * Columns: `id` (PK), `user_id` (FK `users.user_id`, CASCADE), `ticker` (VARCHAR(50), Index), `stock_name` (VARCHAR(255)), `position_type` (VARCHAR(10) — 'LONG'/'SHORT'), `quantity` (INT), `entry_price` (FLOAT), `closing_price` (FLOAT), `total_investment` (FLOAT), `realized_pnl` (FLOAT), `status` (VARCHAR(20) — 'OPEN'/'CLOSED'), `close_type` (VARCHAR(20)), `exit_reason` (VARCHAR(20)), `take_profit` (FLOAT), `stop_loss` (FLOAT), `tp_edit_count` (INT), `sl_edit_count` (INT), `created_at` (TIMESTAMP), `closed_at` (TIMESTAMP).
   * Indexes: `idx_positions_user_status` (`(user_id, status)`).

5. **`orders`** (TP/SL Triggers & AMO Orders)
   * Columns: `id` (PK), `position_id` (FK `positions.id`, CASCADE), `user_id` (FK `users.user_id`, CASCADE), `ticker` (VARCHAR(50), Index), `stock_name` (VARCHAR(255)), `order_type` (VARCHAR(20) — 'TP'/'SL'/'AMO_ENTRY'), `position_type` (VARCHAR(10)), `quantity` (INT), `trigger_price` (FLOAT), `execution_price` (FLOAT), `take_profit` (FLOAT), `stop_loss` (FLOAT), `locked_amount` (FLOAT), `status` (VARCHAR(20) — 'PENDING'/'EXECUTED'/'CANCELLED'), `created_at` (TIMESTAMP), `executed_at` (TIMESTAMP).
   * Indexes: `idx_orders_user_status` (`(user_id, status)`).

6. **`transactions`** (Immutable Financial Audit Ledger)
   * Columns: `id` (PK), `user_id` (FK `users.user_id`, CASCADE), `position_id` (FK `positions.id`, SET NULL), `ticker` (VARCHAR(50)), `stock_name` (VARCHAR(255)), `transaction_type` (VARCHAR(30)), `position_type` (VARCHAR(10)), `quantity` (INT), `price` (FLOAT), `amount` (FLOAT), `pnl` (FLOAT), `timestamp` (TIMESTAMP).

7. **`token_blacklist`**
   * Columns: `id` (PK), `token_jti` (VARCHAR(255), Unique, Index), `blacklisted_at` (TIMESTAMP).

8. **`login_attempts`**
   * Columns: `id` (PK), `email` (VARCHAR(255), Index), `ip_address` (VARCHAR(45)), `user_agent` (VARCHAR(500)), `success` (BOOLEAN), `failure_reason` (VARCHAR(100)), `attempted_at` (TIMESTAMP).

9. **`verification_tokens`**
   * Columns: `id` (PK), `user_id` (FK `users.user_id`, CASCADE), `token_hash` (VARCHAR(255)), `otp_code` (VARCHAR(6)), `token_type` (VARCHAR(50)), `expires_at` (TIMESTAMP), `used` (BOOLEAN), `created_at` (TIMESTAMP).

10. **`market_sessions`** & **`holidays`**
    * Session calendars and official NSE market closure dates.

---

## 8. Quantitative Models & Technical Indicator Formulas

*Source: [`backend/indicator_service.py`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/indicator_service.py)*

1. **Relative Strength Index (RSI - Wilder's Smoothing, $N=14$):**
   $$\Delta P_t = C_t - C_{t-1}, \quad U_t = \max(\Delta P_t, 0), \quad D_t = \max(-\Delta P_t, 0)$$
   $$EMA_{Wilder}(X, N)_t = \frac{X_t + (N-1) \cdot EMA_{t-1}}{N}$$
   $$RS = \frac{EMA_{Wilder}(U, 14)}{EMA_{Wilder}(D, 14)}, \quad RSI = 100 - \frac{100}{1 + RS}$$

2. **Moving Average Convergence Divergence (MACD - 12, 26, 9):**
   $$MACD_{line} = EMA_{12}(Close) - EMA_{26}(Close)$$
   $$Signal_{line} = EMA_9(MACD_{line})$$
   $$Histogram = MACD_{line} - Signal_{line}$$

3. **Bollinger Bands (Period $N=20$, Multiplier $K=2$):**
   $$MiddleBand = SMA_{20}(Close) = \frac{1}{20}\sum_{i=0}^{19} Close_{t-i}$$
   $$\sigma = \sqrt{\frac{1}{20}\sum_{i=0}^{19} (Close_{t-i} - MiddleBand)^2}$$
   $$UpperBand = MiddleBand + 2\sigma, \quad LowerBand = MiddleBand - 2\sigma$$

4. **Average True Range (ATR - $N=14$):**
   $$TR_t = \max\left( High_t - Low_t, \, |High_t - Close_{t-1}|, \, |Low_t - Close_{t-1}| \right)$$
   $$ATR_t = EMA_{Wilder}(TR, 14)_t$$

5. **Supertrend ($N=10$, Multiplier $M=3.0$):**
   $$BasicUpper = \frac{High_t + Low_t}{2} + M \cdot ATR_{10}$$
   $$BasicLower = \frac{High_t + Low_t}{2} - M \cdot ATR_{10}$$

6. **Stochastic Oscillator ($N=14$, Smooth $\%K=3$, Smooth $\%D=3$):**
   $$\%K_{raw} = \frac{Close_t - \min_{14}(Low)}{\max_{14}(High) - \min_{14}(Low)} \times 100$$
   $$\%K = SMA_3(\%K_{raw}), \quad \%D = SMA_3(\%K)$$

---

## 9. Trade Execution Rules & Risk Safety Gates

*Source: [`backend/execution_engine.py`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/execution_engine.py), [`backend/trade_service.py`](file:///c:/Users/sohan/Desktop/StockMarket/StockMarket/StockMarket/backend/trade_service.py)*

### Order Lifecycle & Monitoring Loop
* Execution loop runs every **2.0 seconds** during active market hours (`09:15–15:30 IST`).
* Reads all orders where `status == 'PENDING'` from PostgreSQL in atomic transactions.
* **Long Position TP/SL Execution:**
  * Take Profit Hit: $CMP \ge TargetPrice \implies$ Position closed with `exit_reason = 'TP_HIT'`, `realized_pnl = (CMP - Entry) \times Qty`.
  * Stop Loss Hit: $CMP \le StopLoss \implies$ Position closed with `exit_reason = 'SL_HIT'`, `realized_pnl = (CMP - Entry) \times Qty`.
* **Short Position TP/SL Execution:**
  * Take Profit Hit: $CMP \le TargetPrice \implies$ Position closed with `exit_reason = 'TP_HIT'`, `realized_pnl = (Entry - CMP) \times Qty`.
  * Stop Loss Hit: $CMP \ge StopLoss \implies$ Position closed with `exit_reason = 'SL_HIT'`, `realized_pnl = (Entry - CMP) \times Qty`.

### Hard Safety Gates Summary

| Gate ID | Condition Checked | Parameter / Bound | Default | Action on Breach | Source File:Line |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **GATE-01** | Account Verification | `user.is_verified` | `True` | Rejects order (`403 Forbidden`) | `trade_router.py:45` |
| **GATE-02** | Account Lockout | `failed_login_attempts` | $\ge 5$ fails | Locks account for 15 minutes | `auth.py:225` |
| **GATE-03** | Capital Sufficiency | `virtual_balance >= cost` | $Qty \times Price$ | Rejects order (`400 Insufficient Funds`) | `trade_service.py:90` |
| **GATE-04** | Quantity Sanity | `quantity > 0` | Integer $\ge 1$ | Rejects order (`422 Validation Error`) | `schemas.py:120` |
| **GATE-05** | TP/SL Bound Validity | Long: $SL < Entry < TP$<br>Short: $TP < Entry < SL$ | Price levels | Rejects order (`400 Invalid TP/SL`) | `trade_service.py:115` |
| **GATE-06** | TP/SL Modification Limit| `tp_edit_count`, `sl_edit_count`| $\le 3$ edits | Rejects update (`400 Max Edits Reached`) | `trade_service.py:240` |
| **GATE-07** | WS Subscription Ceiling| `len(_subscribed)` | $\le 2000$ tickers | LRU evicts unviewed ticker | `main.py:240` |
| **GATE-08** | AnyIO Worker Limit | `total_tokens` | `64` threads | Queues sync route requests | `main.py:6806` |
| **GATE-09** | Glibc Malloc Arenas | `MALLOC_ARENA_MAX` | `2` | Binds OS memory allocations | `Dockerfile:35` |
| **GATE-10** | Database Statement Timeout| `statement_timeout` | `30000ms` (30s) | Cancels runaway query | `database.py:62` |

---

## 10. Test Suite Execution & Real Baseline

*Command Executed:* `pytest StockMarket/StockMarket/backend/tests -v`

```text
Total Test Cases:      1,596
Passed:                1,589
Failed (Obsolete):     7 (All traced to superseded legacy static assertions)
Skipped:               0
Subtests Passed:       15
Total Execution Time:  198.37s
```

*Status of 7 Failed Tests:* Fully audited as obsolete assertions (AnyIO token cap 8 vs 64, Smallcap symbol mapping, refactored prefill AST inspection, and removed 3s sleep in backfill recovery). Zero functional or production blockers.

---

## 11. Documented Incidents & Engineering Post-Mortems

1. **INC-01: Startup Hang & Infinite Loading Loop**
   * *Root Cause:* Synchronous daily prefill and token loading holding database sessions, exhausting the initial 10-connection pool.
   * *Fix:* Sized pool to `20` base + `30` overflow, isolated prefill to background async scheduler after 30s delay.
2. **INC-02: Event Loop Stalling & 17-Second Lag Spike**
   * *Root Cause:* Large sequential database table scan in `_sync_daily_base_price` and blocking `yfinance` network I/O executing directly on async event loop.
   * *Fix:* Offloaded all blocking queries to worker threads via `asyncio.to_thread` and dedicated `ThreadPoolExecutor`, reducing loop lag to ~1–2ms.
3. **INC-03: Glibc Malloc Arena Memory Growth**
   * *Root Cause:* Multi-threaded FastAPI sync route dispatch creating separate glibc heap arenas (up to 8x core count), causing 400MB+ unreleased RSS growth.
   * *Fix:* Baked `MALLOC_ARENA_MAX=2` into container environment and set AnyIO thread limiter cap to 64.
4. **INC-04: Hourly Rollover Aggregator Crash**
   * *Root Cause:* Variable name collision (`flushed_1h` instead of `completed_1h`) throwing runtime `NameError` during hourly candle aggregation.
   * *Fix:* Corrected variable reference and added hourly rollover unit tests.
5. **INC-05: JWT Expiration 5.5-Hour Timezone Offset**
   * *Root Cause:* Naive IST datetime passed into `python-jose` which expects UTC, causing tokens to live 29.5 hours instead of 24.
   * *Fix:* Replaced with `datetime.now(timezone.utc)`.
6. **INC-06: Search Dropdown 0.00% Price Lag**
   * *Root Cause:* Asynchronous metadata load without base price resolution leaving newly viewed tickers at 0.00% until first tick.
   * *Fix:* Added `_sync_daily_base_price` to write latest close to `stock_metadata.base_price` and implemented client-side `_fetchNavSearchLivePrices` batch querying.
