import urllib.request
import json
import os
import concurrent.futures
import time

# Configuration
TV_SCANNER_URL = "https://scanner.tradingview.com/india/scan"

# Database Imports
from database import SessionLocal
from models import StockMetadata, StockData, CurrentDayCandle

# Robust Path Resolution
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# OUTPUT_PATH removed (we update DB now)
LOGO_DIR = os.path.join(BASE_DIR, "..", "frontend", "logos")

# Ensure Logo Directory Exists
if not os.path.exists(LOGO_DIR):
    os.makedirs(LOGO_DIR)

# Helper: Download Logo
def download_logo(task):
    url, path, ticker = task
    try:
        # User-Agent is sometimes needed
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response, open(path, 'wb') as out_file:
            out_file.write(response.read())
        return (ticker, True)
    except Exception as e:
        return (ticker, False)

def sync_market_data():
    print(f"Fetching data from TradingView Scanner ({TV_SCANNER_URL})...")
    
    # TradingView Payload (Max Range)
    payload = {
        "filter": [
            {"left": "type", "operation": "in_range", "right": ["stock", "dr", "fund"]},
            {"left": "subtype", "operation": "in_range", "right": ["common", "preference", "etf", "unit", "mutual", "euronext", "trust", "reit"]} 
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}},
        "columns": ["name", "description", "exchange", "close", "type", "subtype", "logoid"],
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 8000] # Increase limit to capture new stocks/IPOs
    }

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(TV_SCANNER_URL, data=data, headers={
            'User-Agent': 'Mozilla/5.0',
            'Content-Type': 'application/json'
        })

        with urllib.request.urlopen(req) as response:
            resp_data = response.read().decode('utf-8')
            json_data = json.loads(resp_data)

        data_list = json_data.get('data', [])
        print(f"Fetched {len(data_list)} stocks (Full Market).")

        # 2. Update Database
        db = SessionLocal()
        new_stocks_count = 0
        delisted_stocks_count = 0

        try:
            # Pre-fetch existing tickers to minimize queries
            existing_tickers = {s.ticker for s in db.query(StockMetadata.ticker).all()}
            
            # Extract market tickers from TradingView data
            market_tickers = set()
            for item in data_list:
                d = item.get('d', [])
                if len(d) >= 1 and d[0]:
                    market_tickers.add(d[0])
            
            # ===== DELISTED STOCK DETECTION =====
            # Stocks in DB but NOT in current market = delisted
            # EXCLUDE INDICES from deletion (they're not in TradingView scanner)
            PROTECTED_TICKERS = {'NIFTY', 'BANKNIFTY', 'SENSEX'}
            delisted_tickers = (existing_tickers - market_tickers) - PROTECTED_TICKERS
            
            if delisted_tickers:
                print(f"[Sync] Detected {len(delisted_tickers)} potentially delisted stocks: {list(delisted_tickers)[:10]}...")
                
                # Remove from stock_metadata
                db.query(StockMetadata).filter(StockMetadata.ticker.in_(delisted_tickers)).delete(synchronize_session=False)
                
                # Remove historical data for delisted stocks
                db.query(StockData).filter(StockData.ticker.in_(delisted_tickers)).delete(synchronize_session=False)
                
                # Remove current day candles for delisted stocks
                db.query(CurrentDayCandle).filter(CurrentDayCandle.ticker.in_(delisted_tickers)).delete(synchronize_session=False)
                
                delisted_stocks_count = len(delisted_tickers)
                print(f"[Sync] Removed {delisted_stocks_count} delisted stocks and their data.")

            # ===== NEW STOCK ADDITION =====
            # Process Download Tasks & DB Updates
            for item in data_list:
                d = item.get('d', [])
                if len(d) >= 7:
                    symbol = d[0]
                    name = d[1]
                    exchange = d[2]
                    price = d[3] if d[3] else 0
                    logoid = d[6]
                    
                    if not symbol or not name: continue

                    # Determine Logo
                    logo_url = symbol[:2] # Fallback
                    
                    # Check for local logo first
                    local_filename = f"{symbol}.svg"
                    local_path = os.path.join(LOGO_DIR, local_filename)
                    public_path = f"logos/{local_filename}"

                    if os.path.exists(local_path):
                         logo_url = public_path
                    elif logoid:
                         # Queue download if not exists (and we want it?)
                         # For now, store the TV URL in DB if local missing
                         # Or we can download it. 
                         # Let's use the TV URL directly for DB to save space/time, 
                         # unless we want local caching.
                         # User liked TV URLs for Reliability.
                         logo_url = f"https://s3-symbol-logo.tradingview.com/{logoid}.svg"
                    
                    # DB Upsert
                    if symbol in existing_tickers:
                        # Optional: Update Name/Price? 
                        # db_stock = db.query(StockMetadata).filter_by(ticker=symbol).first()
                        # db_stock.base_price = price
                        pass
                    else:
                        new_stock = StockMetadata(
                            ticker=symbol,
                            name=name,
                            exchange=exchange,
                            base_price=price,
                            logo=logo_url
                        )
                        db.add(new_stock)
                        existing_tickers.add(symbol)
                        new_stocks_count += 1
            
            db.commit()
            print(f"Sync Complete. Added {new_stocks_count} new stocks. Removed {delisted_stocks_count} delisted stocks.")
            return {"new": new_stocks_count, "delisted": delisted_stocks_count}

        except Exception as e:
            db.rollback()
            print(f"DB Error: {e}")
            raise e
        finally:
            db.close()


    except Exception as e:
        print(f"Error: {e}")
        return 0

if __name__ == "__main__":
    sync_market_data()
