import os
import sys
import time
from datetime import datetime
from sqlalchemy import text
from database import SessionLocal, engine
from historical_service import historical_service
from angelone_service import angelone_service

def fetch_1h_nov_dec():
    if not historical_service.login():
        print("Historical API Login Failed")
        return
        
    angelone_service.login()
    angelone_service.load_instruments()
    
    FEATURED_STOCKS = [
        'NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 
        'ICICIBANK', 'BHARTIARTL', 'ITC', 'KOTAKBANK', 'LT', 'AXISBANK', 'SBIN', 
        'HINDUNILVR', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'SUNPHARMA', 
        'ULTRACEMCO', 'TATAMOTORS', 'TATASTEEL', 'WIPRO', 'NESTLEIND', 'ADANIENT', 
        'ADANIPORTS', 'M&M', 'ONGC', 'NTPC', 'POWERGRID', 'JSWSTEEL', 'TECHM', 
        'LTIM', 'HDFCLIFE', 'SBILIFE', 'DRREDDY', 'CIPLA', 'APOLLOHOSP', 'BRITANNIA', 
        'INDUSINDBK', 'EICHERMOT', 'DIVISLAB', 'BAJAJ-AUTO', 'HEROMOTOCO', 'TATACONSUM', 
        'GRASIM', 'UPL', 'ALKEM', 'ZOMATO', 'PAYTM', 'DLF', 'HAL', 'BEL', 'TRENT', 
        'VEDL', 'IOC', 'GAIL', 'SHREECEM'
    ]
    
    tickers = [t + '.NS' if t not in ['NIFTY', 'BANKNIFTY', 'SENSEX'] else t for t in FEATURED_STOCKS]

    print(f"Found {len(tickers)} featured tickers to fetch 1h data for Nov-Dec 2025.")
    
    start_date = datetime(2025, 11, 1)
    end_date = datetime(2025, 12, 31, 23, 59, 59)
    
    for ticker in tickers:
        print(f"Processing 1H data for {ticker}...")
        db = SessionLocal()
        try:
            clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
            
            # Fetch 1-hour data
            candles_1h = historical_service.get_historical_candles(
                ticker=clean_ticker, interval="ONE_HOUR",
                from_date=start_date, to_date=end_date, exchange="NSE"
            )
            time.sleep(0.4)
            if candles_1h:
                valid_1h = []
                for c in candles_1h:
                    ts = c['timestamp']
                    if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                    valid_1h.append({
                        "ticker": clean_ticker, "timestamp": ts,
                        "open": float(c['open']), "high": float(c['high']),
                        "low": float(c['low']), "close": float(c['close']),
                        "volume": int(c['volume'])
                    })
                if valid_1h:
                    db.execute(text("""
                        INSERT INTO intraday_candles_1h (ticker, timestamp, open, high, low, close, volume)
                        VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                        ON CONFLICT (ticker, timestamp) DO UPDATE SET
                            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                            close = EXCLUDED.close, volume = EXCLUDED.volume;
                    """), valid_1h)
                    db.commit()
                    print(f"  -> Saved {len(valid_1h)} candles.")
                else:
                    print(f"  -> No valid candles.")
            else:
                print(f"  -> No data returned from Angel One.")
        except Exception as e:
            print(f"Error processing {ticker}: {e}")
        finally:
            db.close()

if __name__ == "__main__":
    fetch_1h_nov_dec()
