import sys
import time
import os
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from angelone_service import angelone_service
from database import SessionLocal
from models import StockMetadata, StockData, IntradayCandle1H
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text

def fetch_bulk_history(days_1d=730, days_1h=365):
    print("Initializing Angel One connection...", flush=True)
    if not angelone_service.login():
        print("Failed to login to Angel One.", flush=True)
        return
    angelone_service.load_instruments()

    session = SessionLocal()
    try:
        # Get exactly the active tickers that already have 5m data
        tickers = session.execute(text("SELECT DISTINCT ticker FROM intraday_candles_5min")).fetchall()
        full_ticker_list = [t[0] for t in tickers]
        
        # Filter out tickers that have already been fetched (to save API calls when resuming)
        already_fetched = session.execute(text("SELECT DISTINCT ticker FROM stock_data")).fetchall()
        fetched_set = set(t[0] for t in already_fetched)
        
        ticker_list = [t for t in full_ticker_list if t not in fetched_set]
        
        total = len(ticker_list)
        print(f"Found {len(full_ticker_list)} active tickers. {len(fetched_set)} already fetched. Resuming with {total} remaining.", flush=True)
        print(f"Fetching {days_1d} days of 1D data AND {days_1h} days of 1H data.", flush=True)

        end_date = datetime.now()
        start_1d = end_date - timedelta(days=days_1d)
        
        # Angel One API requires strict market hours for historical fetch or it returns []
        end_str = end_date.strftime("%Y-%m-%d") + " 15:30"
        start_1d_str = start_1d.strftime("%Y-%m-%d") + " 09:15"

        total_1d = 0
        total_1h = 0

        for i, ticker in enumerate(ticker_list):
            ticker_clean = ticker.replace('.NS', '').replace('.BO', '')
            token_data = angelone_service.get_token(ticker_clean)
            
            if not token_data:
                continue
                
            # Crucial Fix: Use the exact exchange from the token (AngelOne requires NSE token to be queried as NSE)
            exchange = token_data.get('exch_seg', 'NSE')
            
            # --- FETCH 1D DATA ---
            params_1d = {
                "exchange": exchange,
                "symboltoken": token_data['token'],
                "interval": "ONE_DAY",
                "fromdate": start_1d_str,
                "todate": end_str
            }
            try:
                resp_1d = angelone_service.smart_api.getCandleData(params_1d)
                if resp_1d and resp_1d.get('status') and resp_1d.get('data'):
                    insert_vals = []
                    for c in resp_1d['data']:
                        ts = datetime.strptime(c[0], "%Y-%m-%dT%H:%M:%S%z")
                        insert_vals.append({
                            "ticker": ticker, "open": float(c[1]), "high": float(c[2]),
                            "low": float(c[3]), "close": float(c[4]), "volume": int(c[5]) if c[5] else 0,
                            "date": ts.date()
                        })
                    if insert_vals:
                        stmt = pg_insert(StockData).values(insert_vals)
                        session.execute(stmt.on_conflict_do_nothing())
                        session.commit()
                        total_1d += len(insert_vals)
                else:
                    if i < 3: # Print first 3 errors to debug why it's returning 0
                        print(f"DEBUG {ticker} 1D Resp: {resp_1d}", flush=True)
            except Exception as e:
                session.rollback()
            
            time.sleep(0.35) # Rate limit
            
            # --- FETCH 1H DATA ---
            # 1 year of 1H data is > 1000 candles. Must fetch in batches (100 days per batch)
            current_to = end_date
            target_from = end_date - timedelta(days=days_1h)
            
            while current_to > target_from:
                batch_from = current_to - timedelta(days=100)
                if batch_from < target_from:
                    batch_from = target_from
                
                params_1h = {
                    "exchange": exchange,
                    "symboltoken": token_data['token'],
                    "interval": "ONE_HOUR",
                    "fromdate": batch_from.strftime("%Y-%m-%d") + " 09:15",
                    "todate": current_to.strftime("%Y-%m-%d") + " 15:30"
                }
                
                try:
                    resp_1h = angelone_service.smart_api.getCandleData(params_1h)
                    if resp_1h and resp_1h.get('status') and resp_1h.get('data'):
                        insert_vals_1h = []
                        for c in resp_1h['data']:
                            ts = datetime.strptime(c[0], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
                            insert_vals_1h.append({
                                "ticker": ticker, "timestamp": ts, "open": float(c[1]), 
                                "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), 
                                "volume": int(c[5]) if c[5] else 0
                            })
                        if insert_vals_1h:
                            stmt = pg_insert(IntradayCandle1H).values(insert_vals_1h)
                            session.execute(stmt.on_conflict_do_nothing())
                            session.commit()
                            total_1h += len(insert_vals_1h)
                    else:
                        if i < 3: # Debug 1H errors
                            print(f"DEBUG {ticker} 1H Resp: {resp_1h}", flush=True)
                except Exception as e:
                    session.rollback()

                current_to = batch_from
                time.sleep(0.35) # Rate limit

            # Status Update
            if (i + 1) % 5 == 0:
                print(f"[{i+1}/{total}] {ticker} - Running Total: {total_1d} (1D) | {total_1h} (1H)", flush=True)

        print("\n--- BULK FETCH COMPLETE ---", flush=True)
        print(f"Total 1D Candles: {total_1d}", flush=True)
        print(f"Total 1H Candles: {total_1h}", flush=True)

    finally:
        session.close()

if __name__ == "__main__":
    fetch_bulk_history(365, 180)
