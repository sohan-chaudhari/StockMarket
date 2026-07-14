import database, models
from sqlalchemy.orm import Session

def check_intraday_data(ticker="RELIANCE"):
    db = database.SessionLocal()
    try:
        print(f"Checking data for {ticker} in 5min table...")
        rows_5m = db.query(models.IntradayCandle5Min).filter(models.IntradayCandle5Min.ticker == ticker).all()
        print(f"Total rows (5m): {len(rows_5m)}")
        null_ts_5m = [r.id for r in rows_5m if r.timestamp is None]
        print(f"Rows with null timestamp (5m): {null_ts_5m}")
        
        print(f"Checking data for {ticker} in 15min table...")
        rows_15m = db.query(models.IntradayCandle15Min).filter(models.IntradayCandle15Min.ticker == ticker).all()
        print(f"Total rows (15m): {len(rows_15m)}")
        null_ts_15m = [r.id for r in rows_15m if r.timestamp is None]
        print(f"Rows with null timestamp (15m): {null_ts_15m}")
        
        if rows_15m:
            for r in rows_15m[:10]:
                print(f"Sample: {r.timestamp}, {r.open}, {r.close}")
                
    finally:
        db.close()

if __name__ == "__main__":
    check_intraday_data()
