import time
from datetime import datetime
import re

file_path = r'c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend\main.py'
with open(file_path, 'r', encoding='utf-8') as f:
    code = f.read()

new_general = """
@app.get("/api/news/general")
async def proxy_news_general(db: Session = Depends(get_db)):
    global _news_general_cache, _news_general_cache_ts
    now_ts = time.time()
    if _news_general_cache is not None and (now_ts - _news_general_cache_ts) < _NEWS_CACHE_TTL:
        return _news_general_cache
    try:
        articles = []
        ticker_list = MOVER_TICKERS[:25]
        
        with angelone_service.latest_ticks_lock:
            live_ticks = dict(angelone_service.latest_ticks)
            
        today_date = datetime.now(IST).strftime("%Y-%m-%d")

        # fallback DB (fetch only latest row per ticker, using thread to avoid blocking event loop)
        def fetch_latest():
            res = {}
            for t in ticker_list:
                row = db.query(models.StockData).filter(models.StockData.ticker == t).order_by(models.StockData.date.desc()).first()
                if row:
                    res[t] = row
            return res
        
        latest_by_ticker = await asyncio.to_thread(fetch_latest)

        # ── Stock-level articles with sector context ──
        stock_changes = []
        has_data = False
        for t in ticker_list:
            n = (STOCK_META.get(t) or {}).get('name', t)
            sector = _SECTOR_MAP.get(t, "")
            sector_tag = f" ({sector})" if sector else ""
            
            # Prefer Live data
            tick = live_ticks.get(t)
            if tick and tick.get('last_traded_price') and tick.get('change_per'):
                has_data = True
                chg = tick['change_per']
                cp = tick['last_traded_price']
                high = tick.get('high', cp)
                low = tick.get('low', cp)
                vol = tick.get('volume_trade_for_the_day', 0)
                
                direction = "gained" if chg >= 0 else "lost"
                stock_changes.append((t, n, sector, chg, cp))
                articles.append({
                    "title": f"{n}{sector_tag} {direction} {abs(chg):.2f}% on {today_date}",
                    "summary": f"{n} traded at ₹{cp:.2f} | High: ₹{high:.2f} Low: ₹{low:.2f} | Vol: {int(vol):,}",
                    "sentiment": "positive" if chg >= 0 else "negative",
                    "source": "Live Market Data",
                    "ticker": t,
                    "url": "",
                    "published_at": today_date
                })
            else:
                row = latest_by_ticker.get(t)
                if row and row.close and row.open:
                    has_data = True
                    chg = ((row.close - row.open) / row.open) * 100
                    direction = "gained" if chg >= 0 else "lost"
                    stock_changes.append((t, n, sector, chg, row.close))
                    articles.append({
                        "title": f"{n}{sector_tag} {direction} {abs(chg):.2f}% on {row.date}",
                        "summary": f"{n} closed at ₹{row.close:.2f} | High: ₹{row.high:.2f} Low: ₹{row.low:.2f} | Vol: {int(row.volume or 0):,}",
                        "sentiment": "positive" if chg >= 0 else "negative",
                        "source": "Market Data",
                        "ticker": t,
                        "url": "",
                        "published_at": str(row.date)
                    })

        # ── Market summary from indices ──
        try:
            nifty_tick = live_ticks.get('NIFTY')
            if nifty_tick and nifty_tick.get('last_traded_price') and nifty_tick.get('change_per'):
                nifty_chg = nifty_tick['change_per']
                nifty_dir = "gained" if nifty_chg >= 0 else "declined"
                nifty_close = nifty_tick['last_traded_price']
                nifty_low = nifty_tick.get('low', nifty_close)
                nifty_high = nifty_tick.get('high', nifty_close)
                
                articles.insert(0, {
                    "title": f"Market roundup: Nifty {nifty_dir} {abs(nifty_chg):.2f}% to {nifty_close:.2f}",
                    "summary": f"Sensex {nifty_dir}. Nifty range: {nifty_low:.2f} - {nifty_high:.2f}. Banking, IT, and Auto among key movers.",
                    "sentiment": "positive" if nifty_chg >= 0 else "negative",
                    "source": "Live Market Summary",
                    "ticker": "NIFTY",
                    "url": "",
                    "published_at": today_date
                })
            else:
                def fetch_indices():
                    indices = ['NIFTY', 'SENSEX', 'BANKNIFTY']
                    res = {}
                    for i in indices:
                        r = db.query(models.StockData).filter(models.StockData.ticker == i).order_by(models.StockData.date.desc()).first()
                        if r: res[i] = r
                    return res
                
                idx_map = await asyncio.to_thread(fetch_indices)
                nifty = idx_map.get('NIFTY')
                if nifty and nifty.close and nifty.open:
                    nifty_chg = ((nifty.close - nifty.open) / nifty.open) * 100
                    nifty_dir = "gained" if nifty_chg >= 0 else "declined"
                    articles.insert(0, {
                        "title": f"Market roundup: Nifty {nifty_dir} {abs(nifty_chg):.2f}% to {nifty.close:.2f}",
                        "summary": f"Sensex {nifty_dir}. Nifty range: {nifty.low:.2f} - {nifty.high:.2f}. Banking, IT, and Auto among key movers.",
                        "sentiment": "positive" if nifty_chg >= 0 else "negative",
                        "source": "Market Summary",
                        "ticker": "NIFTY",
                        "url": "",
                        "published_at": str(nifty.date)
                    })
        except Exception as e:
            print(f"[News] Market summary error: {e}")

        # Top gainer / top loser articles
        try:
            gainers = sorted([sc for sc in stock_changes if sc[3] > 0], key=lambda x: -x[3])
            losers = sorted([sc for sc in stock_changes if sc[3] < 0], key=lambda x: x[3])
            if gainers:
                g = gainers[0]
                articles.insert(1, {
                    "title": f"Top gainer: {g[1]} surges {g[3]:.2f}%{f' ({g[2]})' if g[2] else ''}",
                    "summary": f"{g[1]} was the top gainer among major stocks, trading at ₹{g[4]:.2f}.",
                    "sentiment": "positive",
                    "source": "Market Movers",
                    "ticker": g[0],
                    "url": "",
                    "published_at": today_date
                })
            if losers:
                l = losers[0]
                articles.insert(2, {
                    "title": f"Top loser: {l[1]} drops {abs(l[3]):.2f}%{f' ({l[2]})' if l[2] else ''}",
                    "summary": f"{l[1]} was the top loser among major stocks, trading at ₹{l[4]:.2f}.",
                    "sentiment": "negative",
                    "source": "Market Movers",
                    "ticker": l[0],
                    "url": "",
                    "published_at": today_date
                })
                
            # Sector performance article
            sector_changes = {}
            for t, n, sec, chg, cp in stock_changes:
                if not sec: continue
                if sec not in sector_changes:
                    sector_changes[sec] = []
                sector_changes[sec].append(chg)
            if sector_changes:
                sec_avg = {s: sum(v)/len(v) for s, v in sector_changes.items()}
                best_sec = max(sec_avg, key=sec_avg.get)
                worst_sec = min(sec_avg, key=sec_avg.get)
                if best_sec == worst_sec:
                    best_sec = list(sec_avg.keys())[0]
                articles.insert(3, {
                    "title": f"{best_sec} leads, {worst_sec} lags — sector performance recap",
                    "summary": f"{best_sec} sector avg: {sec_avg[best_sec]:+.2f}% | {worst_sec} sector avg: {sec_avg[worst_sec]:+.2f}%. Track sector leaders for individual stock moves.",
                    "sentiment": "positive" if sec_avg.get('NIFTY', 0) >= 0 else "negative",
                    "source": "Sector Analysis",
                    "ticker": "",
                    "url": "",
                    "published_at": today_date
                })
        except Exception as e:
            print(f"[News] Gainer/Loser/Sector error: {e}")

        # ── Fallback to dummy data when no data ──
"""

start_idx = code.find('@app.get("/api/news/general")')
end_idx = code.find('        # ── Fallback to dummy data when no data ──', start_idx)

if start_idx != -1 and end_idx != -1:
    code = code[:start_idx] + new_general + code[end_idx:]
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(code)
    print("Replaced get_general_news efficiently")
else:
    print("Could not find get_general_news indices")
