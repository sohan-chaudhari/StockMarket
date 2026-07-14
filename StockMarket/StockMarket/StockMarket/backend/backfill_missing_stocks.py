import os
import sys
import time
from datetime import datetime, date, timedelta
from sqlalchemy import text
from backend.database import SessionLocal, engine
from backend.historical_service import historical_service
from backend.angelone_service import angelone_service
from backend.resampler import TieredResampler, CandleResampler

def get_all_expected_tickers(db):
    """Fetch list of all active tickers from stock_metadata."""
    # Using the same list as quick_recent_backfill and other files? Let's query stock_metadata
    result = db.execute(text("SELECT ticker FROM stock_metadata"))
    return [row[0] for row in result]

def check_missing_tickers(db, tickers):
    """Return tickers missing data in intraday_candles_5min and stock_data"""
    missing = []
    for ticker in tickers:
        # Check daily data count
        count_daily_res = db.execute(text("SELECT COUNT(*) FROM stock_data WHERE ticker = :t"), {"t": ticker})
        daily_count = count_daily_res.scalar()
        
        # Check 5m data count
        count_5m_res = db.execute(text("SELECT COUNT(*) FROM intraday_candles_5min WHERE ticker = :t"), {"t": ticker})
        count_5m = count_5m_res.scalar()
        
        # Check 15m data count
        count_15m_res = db.execute(text("SELECT COUNT(*) FROM intraday_candles_15min WHERE ticker = :t"), {"t": ticker})
        count_15m = count_15m_res.scalar()
        
        if daily_count < 10 or count_5m < 50 or count_15m < 50:
            missing.append(ticker)
    return missing

def backfill_missing():
    # Login to services
    if not historical_service.login():
        print("Historical API Login Failed. Exiting.")
        return
        
    angelone_service.login()
    angelone_service.load_instruments()
    
    db = SessionLocal()
    try:
        tickers = get_all_expected_tickers(db)
        print(f"Total active tickers from DB: {len(tickers)}")
        
        missing = check_missing_tickers(db, tickers)
        print(f"Identified {len(missing)} missing tickers: {missing}")
        
        if not missing:
            print("No missing tickers found!")
            return
            
        start_date = datetime(2024, 1, 1) # Example fetch boundary
        end_date = datetime.now()
        
        for ticker in missing:
            print(f"\nProcessing missing ticker: {ticker}")
            try:
                clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
                
                print(f"  Fetching 5m historical data for {clean_ticker} from {start_date.date()} to {end_date.date()}...")
                # AngelOne API often limits historical 5m fetching. We'll do a single request for now.
                # If API supports 1+ year, great, otherwise we might need recursive fetching. 
                # Let's try direct first.
                candles_5m = historical_service.get_historical_candles(
                    ticker=clean_ticker, interval="FIVE_MINUTE",
                    from_date=start_date, to_date=end_date, exchange="NSE"
                )
                
                time.sleep(1) # Rate limit mitigation
                
                if not candles_5m:
                    print(f"  No data returned for {clean_ticker}.")
                    continue
                    
                print(f"  Fetched {len(candles_5m)} 5m candles. Applying TieredResampler...")
                
                # Apply tiered resampling
                resampled = TieredResampler.apply_tiered_resampling(candles_5m)
                
                # 1. Store 5m candles
                if resampled['5m']:
                    valid_5m = []
                    for c in resampled['5m']:
                        ts = c['timestamp']
                        if isinstance(ts, str): ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
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
                        print(f"  Stored {len(valid_5m)} 5m candles.")

                # 2. Store 15m candles
                # Resample ALL 5m data >= 2023 to 15m to ensure the 15min table has recent data for charting API limits
                all_15m_candles = CandleResampler.resample_to_15m([c for c in candles_5m if c['timestamp'] >= datetime(2023, 1, 1, tzinfo=c['timestamp'].tzinfo if hasattr(c['timestamp'], 'tzinfo') else None)])
                if all_15m_candles:
                    valid_15m = []
                    for c in all_15m_candles:
                        ts = c['timestamp']
                        if isinstance(ts, str): ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
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
                        print(f"  Stored {len(valid_15m)} 15m candles.")

                # 3. Store 1d candles
                # Our TieredResampler logic creates 1d candles for pre-2020 which won't trigger if fetch is 2024.
                # However, if 1d candles exist, store them. Wait, we should also always store 1d versions for Daily charts!
                # If we are missing stock entirely, we also want its daily candles.
                # Let's query 1d directly from API as well to guarantee daily history, or resample everything to 1d.
                all_1d_candles = CandleResampler.resample_to_1d(candles_5m)
                
                if all_1d_candles:
                    valid_1d = []
                    for c in all_1d_candles:
                        ts = c['timestamp']
                        if isinstance(ts, str): ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        if ts.tzinfo is not None: ts = ts.replace(tzinfo=None)
                        valid_1d.append({
                            "ticker_symbol": clean_ticker, "date": ts.date(),
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
                        print(f"  Stored {len(valid_1d)} 1d (Daily) candles.")

                db.commit()
                print(f"  Successfully committed updates for {ticker}.")

            except Exception as inner_e:
                print(f"  Error processing ticker {ticker}: {inner_e}")
                db.rollback()

    except Exception as e:
        print(f"Fatal error during backfill: {e}")
    finally:
        db.close()

if __name__ == '__main__':
    print("Starting Missing Stocks Backfill Process...")
    backfill_missing()
    print("Process Finished.")
