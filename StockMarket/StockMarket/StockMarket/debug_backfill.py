
import yfinance as yf
from backend import models, database
from datetime import date, timedelta
import pandas as pd

def debug_backfill():
    ticker = "RELIANCE.NS"
    db = database.SessionLocal()
    
    print(f"Checking DB for {ticker}...")
    count = db.query(models.StockData).filter(models.StockData.ticker == ticker).count()
    print(f"Current Count: {count}")
    
    print("Attempting YFinance Download...")
    start_date = "2015-01-01"
    try:
        df = yf.download(ticker, start=start_date, progress=False)
        print(f"Download Result: {df.shape}")
        
        if not df.empty:
            print(df.head())
            print("Columns:", df.columns)
            
            records_to_insert = []
            for index, row in df.iterrows():
                # print(index, row)
                try:
                    # Handle MultiIndex columns if present (yfinance update)
                    def get_val(col):
                        try:
                            val = row[col]
                            if isinstance(val, pd.Series): return float(val.iloc[0])
                            return float(val)
                        except Exception as e:
                            # Fallback for simple index
                            return float(row[col])

                    # Basic check
                    rec = {
                        "ticker": ticker,
                        "date": index.date(),
                        "open": get_val('Open'),
                        "high": get_val('High'),
                        "low": get_val('Low'),
                        "close": get_val('Close'),
                        "volume": int(get_val('Volume'))
                    }
                    records_to_insert.append(rec)
                except Exception as e:
                    print(f"Error parsing row {index}: {e}")
                    break
            
            print(f"Parsed {len(records_to_insert)} records ready for insert.")
        else:
            print("DataFrame is empty!")
            
    except Exception as e:
        print(f"YFinance Failed: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    debug_backfill()
