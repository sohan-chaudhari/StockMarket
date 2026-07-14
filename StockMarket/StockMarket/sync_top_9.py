import yfinance as yf
from backend.database import SessionLocal
from backend import models
from datetime import datetime, timedelta

TICKERS = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL']

# Mapping for yfinance
YF_MAPPING = {
    'NIFTY': '^NSEI',
    'BANKNIFTY': '^NSEBANK',
    'SENSEX': '^BSESN',
    'RELIANCE': 'RELIANCE.NS',
    'TCS': 'TCS.NS',
    'HDFCBANK': 'HDFCBANK.NS',
    'INFY': 'INFY.NS',
    'ICICIBANK': 'ICICIBANK.NS',
    'BHARTIARTL': 'BHARTIARTL.NS'
}

def sync_history():
    db = SessionLocal()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=10) # Get enough buffer for 4 days
    
    for ticker in TICKERS:
        yf_ticker = YF_MAPPING.get(ticker, ticker)
        print(f"Fetching {ticker} ({yf_ticker})...")
        
        try:
            df = yf.download(yf_ticker, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
            if df.empty:
                print(f"No data for {ticker}")
                continue
            
            for index, row in df.iterrows():
                # Convert index (Timestamp) to date
                date_val = index.date()
                
                # Check if exists
                existing = db.query(models.StockData).filter(
                    models.StockData.ticker == ticker,
                    models.StockData.date == date_val
                ).first()
                
                if not existing:
                    new_data = models.StockData(
                        ticker=ticker,
                        date=date_val,
                        open=float(row['Open']),
                        high=float(row['High']),
                        low=float(row['Low']),
                        close=float(row['Close']),
                        volume=int(row['Volume'])
                    )
                    db.add(new_data)
                    print(f"Added {ticker} for {date_val}")
            
            db.commit()
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")
            db.rollback()
            
    db.close()

if __name__ == "__main__":
    sync_history()
