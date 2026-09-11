# Presentation Script — Stock Market Dashboard

> Speak time: ~2-3 minutes. Read naturally, don't memorize word-for-word.

---

## 1. What I built (30 seconds)

"Sir, I built a **full-stack stock market platform** for the Indian market (NSE), made to rival trading tools like TradingView.

It has **two parts**:
1. **Main trading platform** — real-time charts, live prices, portfolio, watchlist, trading.
2. **News & Sentiment engine** — automatically scrapes financial news and tells whether the market mood is positive or negative.

Everything runs together — both backends launch with one script, and both frontends connect to live data."

---

## 2. Tech stack (20 seconds)

"Backend: **Python FastAPI** + WebSockets for live streaming, with **Angel One API** as the primary data source and **yfinance** as an automatic fallback so the chart never goes empty.

Frontend: **React + Vite**, with **TradingView's Advanced Charts** for professional-grade candlestick charts.

News side: **Celery** background workers + **Playwright/Google News/ScanX scrapers**, plus an ML-based sentiment analyzer.

Data is stored in **SQLite/PostgreSQL** with a candle-aggregation engine that builds 1-min to 1-month candles from raw ticks."

---

## 3. Key features (40 seconds)

"1. **Live candlestick charts** — 60fps rendering, multiple timeframes (1m to 1M), zoom/pan, volume.
2. **Custom drawing tools** — I built my own drawing engine: trendlines, rays, rectangles, Fibonacci tools, with undo/redo, snap-to-price, and selection — like TradingView's toolbar.
3. **Real-time updates** — prices stream over WebSocket (not polling), so data appears instantly.
4. **Portfolio & paper trading** — place/close positions, track P&L, open & closed positions.
5. **Watchlist & market movers** — sector leaders, top gainers, FII/DII data.
6. **News + Sentiment** — headlines per stock with a positive/negative score, scraped on a schedule."

---

## 4. How much is finished (40 seconds)

| Module | Status | Notes |
|--------|--------|-------|
| Backend API | **~95% done** | 55+ endpoints, all working |
| Live price streaming (WebSocket) | **Done** | With reconnect + fallback |
| Candlestick chart + timeframes | **Done** | TradingView engine integrated |
| Portfolio / trading (paper) | **~90% done** | Core flow works |
| News scraping + sentiment | **~90% done** | Works, tuning accuracy |
| Drawing engine | **~70% done** | 9 basic tools done; channels, Fibonacci, pitchfork still in progress |
| Mobile / polish | **~60% done** | Core usable, UI refinements ongoing |

"Overall, I'd say the project is about **85% complete** — the core features all work end-to-end. What remains is mostly advanced charting tools, accuracy tuning of the sentiment model, and UI polish."

---

## 5. What I'm most proud of (20 seconds)

"Two things, sir:
1. The **drawing engine** — most people just use a charting library, but I wrote the geometry, hit-testing, and undo/redo myself.
2. The **fallback architecture** — if the main data provider fails, the system automatically switches to another source, so the chart always shows data. It never goes blank."

---

## 6. Anticipated questions (cheat sheet)

**Q: Where does the data come from?**
A: Angel One API live, yfinance as backup. History is backfilled into the database.

**Q: Why not just use TradingView?**
A: It's commercial and paid. I built my own charting + drawing tools on top of a free chart engine, and added portfolio/trading/news features TradingView doesn't give students.

**Q: Is it real money trading?**
A: No — it's paper/demo trading. Safe and great for learning.

**Q: What's the biggest challenge?**
A: Keeping candles accurate in real time — ticks arriving every 200ms must be aggregated correctly into candles, and data sources can be unreliable. That's why I built the fallback system.

**Q: Can you show me the live demo?**
A: Yes — I'll start it now. (Run `start_all.py`, open the dashboard.)
