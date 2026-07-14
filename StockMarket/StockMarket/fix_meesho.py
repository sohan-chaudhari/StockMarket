import os
from datetime import datetime
from sqlalchemy import text
from backend.database import SessionLocal
from backend.resampler import CandleResampler

def fix_ticker_15m_data(ticker):
    db = SessionLocal()
    try:
        print(f"Loading 5m data for {ticker}...")
        results = db.execute(text("SELECT timestamp, open, high, low, close, volume FROM intraday_candles_5min WHERE ticker=:t"), {"t": ticker}).fetchall()
        
        if not results:
            print(f"No 5m data found for {ticker}")
            return
            
        candles_5m = []
        for r in results:
            candles_5m.append({
                "timestamp": r[0] if r[0].tzinfo else r[0].replace(tzinfo=None),
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5]
            })
            
        print(f"Loaded {len(candles_5m)} 5m candles. Resampling to 15m and 1h...")
        
        # Resample
        candles_15m = CandleResampler.resample_to_15m(candles_5m)
        
        if candles_15m:
            valid_15m = []
            for c in candles_15m:
                valid_15m.append({
                    "ticker": ticker,
                    "timestamp": c['timestamp'],
                    "open": float(c['open']),
                    "high": float(c['high']),
                    "low": float(c['low']),
                    "close": float(c['close']),
                    "volume": int(c['volume'])
                })
                
            db.execute(text("""
                INSERT INTO intraday_candles_15min (ticker, timestamp, open, high, low, close, volume)
                VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                ON CONFLICT (ticker, timestamp) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    close = EXCLUDED.close, volume = EXCLUDED.volume;
            """), valid_15m)
            db.commit()
            print(f"Successfully backfilled {len(valid_15m)} 15m candles for {ticker} directly from its 5m table data.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == '__main__':
    fix_ticker_15m_data("MEESHO")
