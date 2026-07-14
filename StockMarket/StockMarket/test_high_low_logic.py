import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend import models, main
from datetime import date

# Setup in-memory DB
engine = create_engine('sqlite:///:memory:')
models.Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

def test_high_low_update():
    ticker = "TEST.NS"
    today = date.today()
    
    # 1. Simulate initial scraped data
    scraped_data_1 = {
        'open': 100.0,
        'high': 105.0,
        'low': 95.0,
        'current_price': 102.0
    }
    
    # Create initial candle
    main.update_intraday_candle_logic(db, ticker, scraped_data_1)
    
    candle = db.query(models.CurrentDayCandle).filter_by(ticker=ticker).first()
    print(f"Initial State: High={candle.high}, Low={candle.low}, Current={candle.current_price}")
    assert candle.high == 105.0
    assert candle.low == 95.0
    
    # 2. Simulate price SPIKE (Higher than scraped High)
    # Scenario: Scraper still says High is 105 (lag), but current price is 110
    scraped_data_2 = {
        'open': 100.0,
        'high': 105.0, # LAG
        'low': 95.0,
        'current_price': 110.0
    }
    
    main.update_intraday_candle_logic(db, ticker, scraped_data_2)
    db.refresh(candle)
    print(f"After Spike: High={candle.high}, Low={candle.low}, Current={candle.current_price}")
    
    # VERIFICATION: High should be updated to 110.0 despite scraper saying 105.0
    if candle.high == 110.0:
        print("PASS: High updated correctly to current price.")
    else:
        print(f"FAIL: High is {candle.high}, expected 110.0")

    # 3. Simulate price CRASH (Lower than scraped Low)
    # Scenario: Scraper still says Low is 95, but price is 90
    scraped_data_3 = {
        'open': 100.0,
        'high': 110.0, # Updated high
        'low': 95.0, # LAG
        'current_price': 90.0
    }
    
    main.update_intraday_candle_logic(db, ticker, scraped_data_3)
    db.refresh(candle)
    print(f"After Crash: High={candle.high}, Low={candle.low}, Current={candle.current_price}")
    
    # VERIFICATION: Low should be updated to 90.0
    if candle.low == 90.0:
        print("PASS: Low updated correctly to current price.")
    else:
        print(f"FAIL: Low is {candle.low}, expected 90.0")

if __name__ == "__main__":
    try:
        test_high_low_update()
    except Exception as e:
        print(f"Test failed with error: {e}")
