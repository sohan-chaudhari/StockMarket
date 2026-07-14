import os
import sys
import time
from datetime import datetime, date
from sqlalchemy import text
from backend.database import SessionLocal, engine
from backend.historical_service import historical_service
from backend.angelone_service import angelone_service

def backfill():
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

    print(f"Found {len(tickers)} featured tickers to backfill from 2026-02-23 to today.")
    
    start_date = datetime(2026, 2, 20)
    end_date = datetime.now()
    
    def process_ticker(ticker):
        print(f"Processing {ticker}...")
        db = SessionLocal()
        try:
            clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
            
            # Fetch 5-minute data
            candles_5m = historical_service.get_historical_candles(
                ticker=clean_ticker, interval="FIVE_MINUTE",
                from_date=start_date, to_date=end_date, exchange="NSE"
            )
            time.sleep(0.4)
            if candles_5m:
                valid_5m = []
                for c in candles_5m:
                    ts = c['timestamp']
                    if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                    valid_5m.append({
                        "ticker": clean_ticker, "timestamp": ts,
                        "open": float(c['open']), "high": float(c['high']),
                        "low": float(c['low']), "close": float(c['close']),
                        "volume": int(c['volume'])
                    })
                if valid_5m:
                    db.execute(text("""
                        INSERT INTO intraday_candles_5min (ticker, timestamp, open, high, low, close, volume)
                        VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                        ON CONFLICT (ticker, timestamp) DO UPDATE SET
                            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                            close = EXCLUDED.close, volume = EXCLUDED.volume;
                    """), valid_5m)
                    db.commit()

            # Fetch 15-minute data
            candles_15m = historical_service.get_historical_candles(
                ticker=clean_ticker, interval="FIFTEEN_MINUTE",
                from_date=start_date, to_date=end_date, exchange="NSE"
            )
            time.sleep(0.4)
            if candles_15m:
                valid_15m = []
                for c in candles_15m:
                    ts = c['timestamp']
                    if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                    valid_15m.append({
                        "ticker": clean_ticker, "timestamp": ts,
                        "open": float(c['open']), "high": float(c['high']),
                        "low": float(c['low']), "close": float(c['close']),
                        "volume": int(c['volume'])
                    })
                if valid_15m:
                    db.execute(text("""
                        INSERT INTO intraday_candles_15min (ticker, timestamp, open, high, low, close, volume)
                        VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                        ON CONFLICT (ticker, timestamp) DO UPDATE SET
                            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                            close = EXCLUDED.close, volume = EXCLUDED.volume;
                    """), valid_15m)
                    db.commit()

            # Fetch 1-Day data
            candles_1d = historical_service.get_historical_candles(
                ticker=clean_ticker, interval="ONE_DAY",
                from_date=start_date.date(), to_date=end_date.date(), exchange="NSE"
            )
            time.sleep(0.4)
            if candles_1d:
                valid_1d = []
                for c in candles_1d:
                    ts = c['timestamp']
                    if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                    valid_1d.append({
                        "ticker_symbol": ticker, "date": ts.date(),
                        "open": float(c['open']), "high": float(c['high']),
                        "low": float(c['low']), "close": float(c['close']),
                        "volume": int(c['volume'])
                    })
                if valid_1d:
                    db.execute(text("""
                        INSERT INTO stock_data (ticker, date, open, high, low, close, volume)
                        VALUES (:ticker_symbol, :date, :open, :high, :low, :close, :volume)
                        ON CONFLICT (ticker, date) DO UPDATE SET
                            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                            close = EXCLUDED.close, volume = EXCLUDED.volume;
                    """), valid_1d)
                    db.commit()
            return True
        except Exception as e:
            print(f"Error for {ticker}: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    for t in tickers:
        process_ticker(t)

    print("Backfill complete!")

if __name__ == "__main__":
    backfill()
