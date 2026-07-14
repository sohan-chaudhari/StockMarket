import os
from sqlalchemy import text
from backend.database import SessionLocal
from backend.resampler import CandleResampler

def global_local_resample():
    db = SessionLocal()
    try:
        # Get all tickers
        result = db.execute(text("SELECT ticker FROM stock_metadata"))
        tickers = [row[0] for row in result]
        
        print(f"Checking {len(tickers)} tickers for local resampling...")
        
        processed = 0
        
        for ticker in tickers:
            # Check 15m count
            count_15m = db.execute(text("SELECT COUNT(1) FROM intraday_candles_15min WHERE ticker = :t"), {"t": ticker}).scalar()
            
            # If 15m is missing but 5m might exist
            if count_15m < 50:
                # Get 5m data
                results = db.execute(text("SELECT timestamp, open, high, low, close, volume FROM intraday_candles_5min WHERE ticker=:t"), {"t": ticker}).fetchall()
                if len(results) > 50:
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
                        
                    # Resample to 15m
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
                            
                        # Insert
                        db.execute(text("""
                            INSERT INTO intraday_candles_15min (ticker, timestamp, open, high, low, close, volume)
                            VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                            ON CONFLICT (ticker, timestamp) DO UPDATE SET
                                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                                close = EXCLUDED.close, volume = EXCLUDED.volume;
                        """), valid_15m)
                        db.commit()
                        processed += 1
                        print(f"[{processed}] Resampled {len(valid_15m)} 15m candles locally for {ticker}")
                
        print(f"\nDone! Successfully processed and resampled 15m data for {processed} tickers.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == '__main__':
    global_local_resample()
