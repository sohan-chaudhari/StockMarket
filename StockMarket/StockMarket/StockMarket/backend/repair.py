import re

path = r"c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend\main.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# We need to find the start of the FIRST fetch_batch_live_data which is around line 792.
# We will match from 'async def fetch_batch_live_data(tickers: List[str]) -> Dict[str, Dict]:'
# down to the start of the next function:
# 'async def update_all_stocks_cache():' or '@app.on_event("startup")'
# Wait, let's just find the first fetch_batch_live_data and the second fetch_batch_live_data!
# My bad replace shoved half of broadcast_ticks into fetch_batch_live_data AND kept the old one?
# No, it just pasted half of broadcast_ticks in the middle.

# Let's cleanly replace the entire fetch_batch_live_data definition to the end of the file or next known block.
pattern = r"async def fetch_batch_live_data\(tickers: List\[str\]\) -> Dict\[str, Dict\]:.*?(?=\nasync def fetch_stock_news|\n@app\.get|\n@app\.post)"

new_func = """async def fetch_batch_live_data(tickers: List[str]) -> Dict[str, Dict]:
    if not tickers: return {}
    
    results = {}

    # Map all tickers (including Indices)
    clean_tickers = []
    ticker_map = {} # CleanTicker -> OriginalKey
    
    for t in tickers:
        clean = t.replace('.NS', '').replace('.BO', '')
        # Handle index prefixes if needed
        if clean == '^NSEI': clean = 'NIFTY'
        elif clean == '^NSEBANK': clean = 'BANKNIFTY'
        elif clean == '^BSESN': clean = 'SENSEX'
        elif clean == '^CNXFIN': clean = 'FINNIFTY'
        
        clean_tickers.append(clean)
        ticker_map[clean] = t

    try:
        # Use efficient batch method from AngelOneService
        batch_results = await angelone_service.get_batch_live_prices(clean_tickers)
        
        for clean_t, data in batch_results.items():
            original_key = ticker_map.get(clean_t)
            if original_key:
                results[original_key] = {
                    'current_price': data['current_price'],
                    'open': data['open'],
                    'high': data['high'],
                    'low': data['low'],
                    'previous_close': data.get('previous_close', data['current_price']),
                    'volume': data.get('volume', 0),
                    'is_finalized': False
                }
    except Exception as e:
        print(f"[BatchFetch] Error: {e}")

    # Fallback to yfinance for any tickers that failed or are missing!
    missing_tickers = [t for t in tickers if t not in results]
    if missing_tickers:
        print(f"[BatchFetch] Missing {len(missing_tickers)} tickers from AngelOne. Falling back to yfinance.")
        try:
            import yfinance as yf
            import asyncio
            import pandas as pd
            
            yf_query_tickers = [resolve_yf_ticker(t) for t in missing_tickers]
            
            def run_yf_download():
                return yf.download(yf_query_tickers, period="1d", interval="1m", group_by="ticker", threads=True, progress=False, timeout=5)
            
            try:
                data = await asyncio.wait_for(asyncio.to_thread(run_yf_download), timeout=10.0)
            except asyncio.TimeoutError:
                print(f"[BatchFetch] yfinance batch download timed out after 10.0 seconds! Skipping.")
                data = pd.DataFrame()
            
            if not data.empty:
                for idx, yf_t in enumerate(yf_query_tickers):
                    t = missing_tickers[idx]
                    try:
                        t_data = data[yf_t] if data.columns.nlevels > 1 else data
                        if not t_data.empty:
                            valid = t_data.dropna(subset=['Close'])
                            if valid.empty: continue
                            last_row = valid.iloc[-1]
                            prev_row = valid.iloc[-2] if len(valid) > 1 else last_row
                            results[t] = {
                                'current_price': float(last_row['Close']),
                                'open': float(last_row['Open']) if pd.notna(last_row['Open']) else float(last_row['Close']),
                                'high': float(last_row['High']) if pd.notna(last_row['High']) else float(last_row['Close']),
                                'low': float(last_row['Low']) if pd.notna(last_row['Low']) else float(last_row['Close']),
                                'close': float(last_row['Close']),
                                'previous_close': float(prev_row['Close']),
                                'volume': int(last_row['Volume']) if pd.notna(last_row['Volume']) else 0,
                                'is_finalized': False
                            }
                    except Exception as ex:
                        pass
        except Exception as e:
            print(f"[BatchFetch] yfinance batch download failed: {e}")
                    
    # Ultimate Fallback: if a ticker is still missing, check the DB StockData
    still_missing = [t for t in tickers if t not in results]
    if still_missing:
        print(f"[BatchFetch] Still missing {len(still_missing)} tickers. Checking local database...")
        db = database.SessionLocal()
        try:
            for t in still_missing:
                clean_t = t.replace('.NS', '').replace('.BO', '')
                if clean_t == '^NSEI': clean_t = 'NIFTY'
                elif clean_t == '^NSEBANK': clean_t = 'BANKNIFTY'
                elif clean_t == '^BSESN': clean_t = 'SENSEX'
                
                # Check StockData for last 2 records (for prev_close)
                db_tickers = [clean_t]
                if clean_t != t:
                    db_tickers.append(t)
                records = db.query(models.StockData).filter(
                    models.StockData.ticker.in_(db_tickers)
                ).order_by(models.StockData.date.desc()).limit(2).all()
                
                if records:
                    record = records[0]
                    prev_close = float(records[1].close) if len(records) > 1 else float(record.close)
                    results[t] = {
                        'current_price': float(record.close),
                        'open': float(record.open),
                        'high': float(record.high),
                        'low': float(record.low),
                        'close': float(record.close),
                        'previous_close': prev_close,
                        'volume': int(record.volume or 0),
                        'is_finalized': True
                    }
                    print(f"[BatchFetch] Database fallback succeeded for {t}: {record.close}")
        except Exception as e:
            print(f"[BatchFetch] Database fallback failed: {e}")
        finally:
            db.close()
            
    return results
"""

# Let's find out where fetch_batch_live_data begins and just truncate the file and append the correct functions up to the end!
# Actually, the file has a lot of routes below fetch_batch_live_data.
# The route @app.get("/api/news/general") is around line 1500? Wait, no. My dump showed @app.get("/api/news/general") at line 50!
# Wait! In the previous Select-String for 'app.get':
# Line 50: @app.get("/api/news/general")
# Line 1584: @app.get("/api/csrf-token")
# Line 792 & 987: fetch_batch_live_data.
# So fetch_batch_live_data is BETWEEN line 67 and 1584.
# What is directly after fetch_batch_live_data?
# It's 'async def process_market_movers_batch' maybe? Or 'async def update_all_stocks_cache'?
# Let's use regex to replace everything from 'async def fetch_batch_live_data' until the NEXT 'async def' or '@app' that is NOT part of fetch_batch_live_data.
# The corrupted block has TWO 'async def fetch_batch_live_data'. We will replace from the FIRST one to the SECOND one, and then keep the rest?
# NO, we will find the EXACT string boundaries.

start_str = "async def fetch_batch_live_data(tickers: List[str]) -> Dict[str, Dict]:"
start_idx = content.find(start_str)

# Find the next major function definition AFTER the start of fetch_batch_live_data + 100 characters
next_def_idx = content.find("async def", start_idx + 100)
# But wait, my corrupted block might contain 'async def'. 
# Let's find the second fetch_batch_live_data!
second_start_idx = content.find(start_str, start_idx + 10)

if second_start_idx != -1:
    # This means the duplication is still there. We should replace from start_idx up to the END of the second fetch_batch_live_data.
    # Where does the second fetch_batch_live_data end? 
    # At the first "async def" after it!
    end_idx = content.find("async def", second_start_idx + 100)
    if end_idx == -1:
        end_idx = content.find("@app.", second_start_idx + 100)
    
    if end_idx != -1:
        new_content = content[:start_idx] + new_func + "\n\n" + content[end_idx:]
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("Repaired successfully using boundary search!")
    else:
        print("Could not find end of second fetch_batch_live_data")
else:
    # No duplication? Then it's just syntax error. 
    end_idx = content.find("async def", start_idx + 100)
    if end_idx == -1: end_idx = content.find("@app.", start_idx + 100)
    if end_idx != -1:
        new_content = content[:start_idx] + new_func + "\n\n" + content[end_idx:]
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("Repaired without duplication found!")
    else:
        print("Could not find end block")
