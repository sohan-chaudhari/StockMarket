import sys
import time
import asyncio
from datetime import datetime, timedelta

# Fix path for standalone execution
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from angelone_service import angelone_service
from database import SessionLocal
from models import StockMetadata, StockData
from sqlalchemy.dialects.postgresql import insert as pg_insert

def fetch_1d_history_for_all(days_back=365):
    print("Initializing Angel One connection...")
    if not angelone_service.login():
        print("Failed to login to Angel One.")
        return
    angelone_service.load_instruments()

    session = SessionLocal()
    try:
        tickers = session.query(StockMetadata.ticker).filter(StockMetadata.is_active == True).all()
        ticker_list = [t[0] for t in tickers]
        total = len(ticker_list)
        print(f"Found {total} active tickers. Starting fetch for past {days_back} days...")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        end_str = end_date.strftime("%Y-%m-%d %H:%M")
        start_str = start_date.strftime("%Y-%m-%d %H:%M")

        success_count = 0
        error_count = 0
        no_data_count = 0
        total_candles_inserted = 0

        for i, ticker in enumerate(ticker_list):
            ticker_clean = ticker.replace('.NS', '').replace('.BO', '')
            token_data = angelone_service.get_token(ticker_clean)
            
            if not token_data:
                # print(f"[{i+1}/{total}] {ticker} - No token found")
                error_count += 1
                continue

            params = {
                "exchange": "NSE" if ticker.endswith('.NS') else "BSE",
                "symboltoken": token_data['token'],
                "interval": "ONE_DAY",
                "fromdate": start_str,
                "todate": end_str
            }

            try:
                response = angelone_service.smart_api.getCandleData(params)
                
                if response and response.get('status') and response.get('data'):
                    candles = response['data']
                    insert_values = []
                    
                    for c in candles:
                        ts = datetime.strptime(c[0], "%Y-%m-%dT%H:%M:%S%z")
                        insert_values.append({
                            "ticker": ticker,
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5]) if c[5] else 0,
                            "date": ts.date()
                        })

                    if insert_values:
                        # Raw insert with ON CONFLICT DO NOTHING assuming (id) is PK, wait date is also PK
                        # Let's try standard SQLAlchemy insert
                        stmt = pg_insert(StockData).values(insert_values)
                        stmt = stmt.on_conflict_do_nothing()
                        session.execute(stmt)
                        session.commit()
                        
                        total_candles_inserted += len(insert_values)
                        success_count += 1
                        if (i+1) % 10 == 0:
                            print(f"[{i+1}/{total}] {ticker} - Inserted {len(insert_values)} candles (Success: {success_count}, Errors: {error_count})")
                    else:
                        no_data_count += 1

                else:
                    no_data_count += 1

            except Exception as e:
                # print(f"[{i+1}/{total}] {ticker} - Error: {e}")
                session.rollback()
                error_count += 1

            # Respect Angel One rate limits
            time.sleep(0.35)

        print("\n--- FETCH COMPLETE ---")
        print(f"Tickers Processed successfully: {success_count}")
        print(f"Tickers with No Data: {no_data_count}")
        print(f"Tickers Failed: {error_count}")
        print(f"Total 1D Candles Inserted: {total_candles_inserted}")

    finally:
        session.close()

if __name__ == "__main__":
    fetch_1d_history_for_all(365)
