import time
from datetime import datetime
import re

file_path = r'c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend\main.py'
with open(file_path, 'r', encoding='utf-8') as f:
    code = f.read()

new_ticker = """
@app.get("/api/news/ticker/{ticker}")
async def proxy_news_ticker(ticker: str, db: Session = Depends(get_db)):
    global _news_ticker_cache
    now = time.time()
    t = ticker.strip().upper().replace('.NS', '')
    cached = _news_ticker_cache.get(t)
    if cached and (now - cached["ts"]) < _NEWS_CACHE_TTL:
        return cached["data"]
    try:
        meta = db.query(models.StockMetadata).filter(models.StockMetadata.ticker == t).first()
        name = meta.name if meta else t
        
        with angelone_service.latest_ticks_lock:
            live_ticks = dict(angelone_service.latest_ticks)
            
        today_date = datetime.now(IST).strftime("%Y-%m-%d")
        
        tick = live_ticks.get(t)
        articles = []
        if tick and tick.get('last_traded_price') and tick.get('change_per'):
            chg = tick['change_per']
            cp = tick['last_traded_price']
            high = tick.get('high', cp)
            low = tick.get('low', cp)
            vol = tick.get('volume_trade_for_the_day', 0)
            
            direction = "gained" if chg >= 0 else "lost"
            articles.append({
                "title": f"{name} {direction} {abs(chg):.2f}% to ₹{cp:.2f}",
                "summary": f"{name} traded between ₹{low:.2f} and ₹{high:.2f}, closing at ₹{cp:.2f}.",
                "sentiment": "positive" if chg >= 0 else "negative",
                "source": "Live Market Data",
                "url": "",
                "published_at": today_date
            })
        else:
            row = db.query(models.StockData).filter(
                models.StockData.ticker == t
            ).order_by(models.StockData.date.desc()).first()
            if row and row.close and row.open:
                chg = ((row.close - row.open) / row.open) * 100
                direction = "gained" if chg >= 0 else "lost"
                articles.append({
                    "title": f"{name} {direction} {abs(chg):.2f}% to ₹{row.close:.2f}",
                    "summary": f"{name} traded between ₹{row.low:.2f} and ₹{row.high:.2f}, closing at ₹{row.close:.2f}.",
                    "sentiment": "positive" if chg >= 0 else "negative",
                    "source": "Market Data",
                    "url": "",
                    "published_at": str(row.date)
                })
                
        result = {"articles": articles}
        _news_ticker_cache[t] = {"data": result, "ts": now}
        return result
    except Exception as e:
        print(f"[News Ticker] Error: {e}")
        return {"articles": []}
"""

start_idx = code.find('@app.get("/api/news/ticker/{ticker}")')
end_idx = code.find('# ==================== DASHBOARD WEBSOCKET ====================', start_idx)

if start_idx != -1 and end_idx != -1:
    code = code[:start_idx] + new_ticker + '\n' + code[end_idx:]
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(code)
    print("Replaced get_ticker_news")
else:
    print("Could not find get_ticker_news indices")
