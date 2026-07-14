import sys
sys.path.append(".")

from app.database import SessionLocal
from app.models import Ticker
from loguru import logger

# MVP Tickers (Top 5 Indian stocks)
TICKERS = [
    {
        "symbol": "RELIANCE",
        "name": "Reliance Industries Limited",
        "isin": "INE002A01018",
        "sector": "Energy",
        "market_cap": 17.5,  # in trillion INR
        "exchange": "NSE"
    },
    {
        "symbol": "TCS",
        "name": "Tata Consultancy Services Limited",
        "isin": "INE467B01029",
        "sector": "IT Services",
        "market_cap": 13.2,
        "exchange": "NSE"
    },
    {
        "symbol": "INFY",
        "name": "Infosys Limited",
        "isin": "INE009A01021",
        "sector": "IT Services",
        "market_cap": 6.8,
        "exchange": "NSE"
    },
    {
        "symbol": "HDFCBANK",
        "name": "HDFC Bank Limited",
        "isin": "INE040A01034",
        "sector": "Banking",
        "market_cap": 12.1,
        "exchange": "NSE"
    },
    {
        "symbol": "ICICIBANK",
        "name": "ICICI Bank Limited",
        "isin": "INE090A01021",
        "sector": "Banking",
        "market_cap": 7.9,
        "exchange": "NSE"
    }
]

def seed_tickers():
    db = SessionLocal()
    try:
        for ticker_data in TICKERS:
            # Check if exists
            existing = db.query(Ticker).filter(Ticker.symbol == ticker_data["symbol"]).first()
            if existing:
                logger.info(f"Ticker {ticker_data['symbol']} already exists, skipping")
                continue
            
            ticker = Ticker(**ticker_data)
            db.add(ticker)
            logger.info(f"Added ticker: {ticker_data['symbol']} - {ticker_data['name']}")
        
        db.commit()
        logger.info("Successfully seeded all tickers!")
    except Exception as e:
        logger.error(f"Error seeding tickers: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_tickers()
