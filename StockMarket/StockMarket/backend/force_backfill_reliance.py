import database, models, historical_service, main
from datetime import datetime, timedelta
from main import IST

def force_backfill(ticker="RELIANCE"):
    db = database.SessionLocal()
    try:
        # Range: March 1 to Now
        start_date = datetime(2026, 3, 1, tzinfo=IST)
        end_date = datetime.now(IST)
        
        print(f"Force backfilling {ticker} from {start_date} to {end_date}...")
        
        if not historical_service.historical_service.is_logged_in:
            historical_service.historical_service.login()
            
        for interval in ["5m", "15m"]:
            print(f"Fetching {interval}...")
            candles = historical_service.historical_service.get_historical_candles(
                ticker=ticker,
                interval="FIVE_MINUTE" if interval == "5m" else "FIFTEEN_MINUTE",
                from_date=start_date,
                to_date=end_date,
                exchange="NSE"
            )
            
            if candles:
                valid_candles = []
                for c in candles:
                    ts = c['timestamp']
                    # Normalize to IST before stripping
                    ts = ts.astimezone(IST).replace(tzinfo=None)
                    
                    valid_candles.append({
                        "ticker": ticker,
                        "timestamp": ts,
                        "open": float(c['open']),
                        "high": float(c['high']),
                        "low": float(c['low']),
                        "close": float(c['close']),
                        "volume": int(c['volume'])
                    })
                
                from sqlalchemy import text
                stmt = text(f"""
                    INSERT INTO {"intraday_candles_5min" if interval == "5m" else "intraday_candles_15min"} 
                    (ticker, timestamp, open, high, low, close, volume)
                    VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume)
                    ON CONFLICT (ticker, timestamp) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume;
                """)
                db.execute(stmt, valid_candles)
                db.commit()
                print(f"Added/Updated {len(valid_candles)} candles for {interval}")
                
    finally:
        db.close()

if __name__ == "__main__":
    force_backfill()
