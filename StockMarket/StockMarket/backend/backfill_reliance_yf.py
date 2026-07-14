import os
import sys
import time
from datetime import datetime, date
import yfinance as yf
from sqlalchemy import text
from backend.database import SessionLocal, engine

def backfill_with_yf(ticker):
    print(f"Backfilling {ticker} using Yahoo Finance...")
    db = SessionLocal()
    
    yf_ticker = f"{ticker}.NS"
    try:
        # Fetch 15-minute data
        print("Fetching 15m data...")
        df_15m = yf.download(yf_ticker, interval="15m", period="1mo", auto_adjust=False, multi_level_index=False)
        if not df_15m.empty:
            valid_15m = []
            for index, row in df_15m.iterrows():
                ts = index
                if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                valid_15m.append({
                    "ticker": ticker, "timestamp": ts,
                    "open": float(row['Open']), "high": float(row['High']),
                    "low": float(row['Low']), "close": float(row['Close']),
                    "volume": int(row['Volume'])
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
                print(f"Inserted {len(valid_15m)} 15m candles")

        # Fetch 5-minute data
        print("Fetching 5m data...")
        df_5m = yf.download(yf_ticker, interval="5m", period="1mo", auto_adjust=False, multi_level_index=False)
        if not df_5m.empty:
            valid_5m = []
            for index, row in df_5m.iterrows():
                ts = index
                if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                valid_5m.append({
                    "ticker": ticker, "timestamp": ts,
                    "open": float(row['Open']), "high": float(row['High']),
                    "low": float(row['Low']), "close": float(row['Close']),
                    "volume": int(row['Volume'])
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
                print(f"Inserted {len(valid_5m)} 5m candles")

        # Fetch 1-Day data
        print("Fetching 1d data...")
        df_1d = yf.download(yf_ticker, interval="1d", period="3mo", auto_adjust=False, multi_level_index=False)
        if not df_1d.empty:
            valid_1d = []
            for index, row in df_1d.iterrows():
                ts = index
                if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                valid_1d.append({
                    "ticker_symbol": ticker, "date": ts.date(),
                    "open": float(row['Open']), "high": float(row['High']),
                    "low": float(row['Low']), "close": float(row['Close']),
                    "volume": int(row['Volume'])
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
                print(f"Inserted {len(valid_1d)} 1d candles")
        
        # Check current max
        res_max = db.execute(text("SELECT max(timestamp) FROM intraday_candles_15min WHERE ticker='RELIANCE'")).scalar()
        print(f"New Max date for RELIANCE: {res_max}")

    except Exception as e:
        print(f"Error for {ticker}: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    backfill_with_yf('RELIANCE')
    backfill_with_yf('TCS')
