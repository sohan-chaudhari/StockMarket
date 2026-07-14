"""
Fetch February 1st, 2026 candles for all stocks from yfinance
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.database import SessionLocal
from backend.models import StockMetadata, StockData
from datetime import date, datetime
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

# Resolve ticker for yfinance
def resolve_yf_ticker(ticker):
    """Convert our ticker format to yfinance format"""
    if ticker == 'NIFTY':
        return '^NSEI'
    elif ticker == 'BANKNIFTY':
        return '^NSEBANK'
    elif ticker == 'SENSEX':
        return '^BSESN'
    elif ticker.endswith('.NS') or ticker.endswith('.BO'):
        return ticker
    else:
        return f"{ticker}.NS"  # Default to NSE

def fetch_feb1_for_ticker(ticker_obj):
    """Fetch Feb 1st data for a single ticker"""
    ticker = ticker_obj.ticker
    yf_ticker = resolve_yf_ticker(ticker)
    
    try:
        stock = yf.Ticker(yf_ticker)
        # Fetch data for Feb 1st specifically
        df = stock.history(start="2026-02-01", end="2026-02-02", auto_adjust=False)
        
        if not df.empty:
            row = df.iloc[0]  # Get first (and only) row
            candle_date = df.index[0].date()
            
            return {
                "ticker": ticker,
                "date": candle_date,
                "open": float(row['Open']),
                "high": float(row['High']),
                "low": float(row['Low']),
                "close": float(row['Close']),
                "adj_close": float(row.get('Adj Close', row['Close'])),
                "volume": int(row['Volume'])
            }
        else:
            print(f"  ⚠ No data for {ticker}")
            return None
            
    except Exception as e:
        print(f"  ✗ Error fetching {ticker}: {e}")
        return None

def main():
    db = SessionLocal()
    
    print("Fetching all stocks from database...")
    all_stocks = db.query(StockMetadata).all()
    print(f"Found {len(all_stocks)} stocks\n")
    
    print("Fetching Feb 1st, 2026 candles from yfinance...")
    print("=" * 60)
    
    records_to_insert = []
    
    # Parallel fetch with progress
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_feb1_for_ticker, stock): stock for stock in all_stocks}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            
            if result:
                records_to_insert.append(result)
            
            # Progress indicator
            if completed % 100 == 0:
                print(f"  Progress: {completed}/{len(all_stocks)} stocks processed...")
    
    print(f"\n✓ Fetched {len(records_to_insert)} candles for Feb 1st")
    
    if records_to_insert:
        print("\nInserting into database...")
        
        # Check for existing Feb 1st data and delete
        existing_count = db.query(StockData).filter(StockData.date == date(2026, 2, 1)).count()
        if existing_count > 0:
            print(f"  Removing {existing_count} existing Feb 1st records...")
            db.query(StockData).filter(StockData.date == date(2026, 2, 1)).delete()
            db.commit()
        
        # Bulk insert
        db.bulk_insert_mappings(StockData, records_to_insert)
        db.commit()
        
        print(f"✓ Successfully inserted {len(records_to_insert)} Feb 1st candles!")
    else:
        print("✗ No data to insert")
    
    db.close()

if __name__ == "__main__":
    main()
