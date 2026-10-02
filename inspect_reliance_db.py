from backend import database, models
from datetime import date

def inspect_db():
    db = database.SessionLocal()
    ticker = "RELIANCE.NS"
    today = date.today()
    
    print(f"Inspecting DB for {ticker} on {today}")
    
    # 1. Current Day Candle
    candle = db.query(models.CurrentDayCandle).filter(
        models.CurrentDayCandle.ticker == ticker,
        models.CurrentDayCandle.trading_date == today
    ).first()
    
    if candle:
        print("\n--- CURRENT DAY CANDLE ---")
        print(f"Open: {candle.open}")
        print(f"High: {candle.high}")
        print(f"Low: {candle.low}")
        print(f"Current: {candle.current_price}")
        print(f"Close: {candle.close}")
        print(f"Finalized: {candle.is_finalized}")
    else:
        print("\nNo CurrentDayCandle found for today")
        
    # 2. Historical Data
    hist = db.query(models.StockData).filter(
        models.StockData.ticker == ticker
    ).order_by(models.StockData.date.desc()).limit(5).all()
    
    print("\n--- HISTORICAL DATA (Last 5) ---")
    for h in hist:
        print(f"Date: {h.date} | O: {h.open} | H: {h.high} | L: {h.low} | C: {h.close}")
        
    db.close()

if __name__ == "__main__":
    inspect_db()
