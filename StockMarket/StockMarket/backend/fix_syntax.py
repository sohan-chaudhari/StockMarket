import re

with open('main.py', 'r') as f:
    content = f.read()

# Let's fix the get_live_prices_batch function entirely.
# Find the start and end of it.
start_idx = content.find('def get_live_prices_batch(request_data: BatchPriceRequest, db: Session = Depends(get_db)):')
end_idx = content.find('def get_top_9_history(db: Session = Depends(get_db)):')
end_idx = content.rfind('@app.get("/api/top-9-history")', start_idx, end_idx)

new_func = '''def get_live_prices_batch(request_data: BatchPriceRequest, db: Session = Depends(get_db)):
    """
    Fetches live prices using batch fetch + cache to dramatically improve frontend loading times.
    """
    try:
        global _live_prices_cache
        now = time.time()
        
        results = {}
        tickers_to_fetch = []
        
        # 1. Check cache first
        for t in request_data.tickers:
            yf_ticker = resolve_yf_ticker(t)
            if yf_ticker in _live_prices_cache and (now - _live_prices_cache[yf_ticker]['ts'] < LIVE_PRICES_CACHE_TTL):
                cached_data = _live_prices_cache[yf_ticker]['data']
                if cached_data is not None:
                    results[t] = cached_data
                # if cached_data is None, intentionally skip to prevent hammering the API
            else:
                tickers_to_fetch.append(t)
                
        # 2. Fetch missing from API
        if tickers_to_fetch:
            mapped_tickers = []
            key_map = {} 
            
            for t in tickers_to_fetch:
                yf_ticker = resolve_yf_ticker(t)
                mapped_tickers.append(yf_ticker)
                key_map[yf_ticker] = t
                
            real_data_map = await fetch_batch_live_data(mapped_tickers)
            
            for yf_t in mapped_tickers:
                original_key = key_map[yf_t]
                if yf_t in real_data_map and real_data_map[yf_t]:
                     prev_close_val = real_data_map[yf_t].get('previous_close', real_data_map[yf_t].get('current_price', 0))
                     data_val = {
                         "current": real_data_map[yf_t].get('current_price', 0),
                         "open": real_data_map[yf_t].get('open', 0),
                         "high": real_data_map[yf_t].get('high', 0),
                         "low": real_data_map[yf_t].get('low', 0),
                         "prev_close": prev_close_val
                     }
                     results[original_key] = data_val
                     _live_prices_cache[yf_t] = {'data': data_val, 'ts': now}

        # Enrich with sector info and change percentages
        if results:
            for t in list(results.keys()):
                d = results[t]
                # Compute changePct from prev_close / current
                prev_close = d.get('prev_close', 0)
                current = d.get('current', 0)
                change_pct = 0
                if prev_close and prev_close > 0:
                    change_pct = round((current - prev_close) / prev_close * 100, 2)
                d['changePct'] = change_pct

                # Look up sector from ticker
                clean_t = t.replace('.NS', '').replace('.BO', '')
                sector = _SECTOR_MAP.get(clean_t, '')
                d['sector'] = sector

                # Look up sector index change for live sector performance
                sector_pct = 0
                if sector and sector in SECTOR_TO_NSE_INDEX:
                    sector_idx = SECTOR_TO_NSE_INDEX[sector]
                    if sector_idx in results:
                        sector_pct = results[sector_idx].get('changePct', 0)
                d['sectorPct'] = sector_pct
                
        return results
    except Exception as e:
        import traceback
        import os
        with open(os.path.join(BASE_DIR, "live_price_error.txt"), "w") as f:
            f.write(traceback.format_exc())
        raise e
'''

content = content[:start_idx] + new_func + '\n' + content[end_idx:]

with open('main.py', 'w') as f:
    f.write(content)
