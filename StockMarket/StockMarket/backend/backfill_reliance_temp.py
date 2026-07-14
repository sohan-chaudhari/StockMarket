import os
import sys
import time
from datetime import datetime, date
from sqlalchemy import text
from backend.database import SessionLocal, engine
from backend.historical_service import historical_service
from backend.angelone_service import angelone_service

def backfill_reliance():
    if not historical_service.login():
        print("Historical API Login Failed")
        return
        
    angelone_service.login()
    # We won't load instruments to save time, relying on fallback
    
    ticker = 'RELIANCE'
    start_date = datetime(2026, 2, 13)
    end_date = datetime.now()
    
    print(f"Processing {ticker} from {start_date} to {end_date}...")
    db = SessionLocal()
    try:
        # Fetch 5-minute data
        candles_5m = historical_service.get_historical_candles(
            ticker=ticker, interval="FIVE_MINUTE",
            from_date=start_date, to_date=end_date, exchange="NSE"
        )
        time.sleep(0.4)
        if candles_5m:
            valid_5m = []
            for c in candles_5m:
                ts = c['timestamp']
                if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                valid_5m.append({
                    "ticker": ticker, "timestamp": ts,
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
                print(f"Inserted {len(valid_5m)} 5m candles")

        # Fetch 15-minute data
        candles_15m = historical_service.get_historical_candles(
            ticker=ticker, interval="FIFTEEN_MINUTE",
            from_date=start_date, to_date=end_date, exchange="NSE"
        )
        time.sleep(0.4)
        if candles_15m:
            valid_15m = []
            for c in candles_15m:
                ts = c['timestamp']
                if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                valid_15m.append({
                    "ticker": ticker, "timestamp": ts,
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
                print(f"Inserted {len(valid_15m)} 15m candles")

        # Fetch 1-Day data
        candles_1d = historical_service.get_historical_candles(
            ticker=ticker, interval="ONE_DAY",
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
                print(f"Inserted {len(valid_1d)} 1d candles")
        return True
    except Exception as e:
        print(f"Error for {ticker}: {e}")
        db.rollback()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    backfill_reliance()
