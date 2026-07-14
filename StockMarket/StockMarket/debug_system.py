import os
import sys
from datetime import date, datetime, timedelta
import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session
from backend import models, database

# Setup DB
db = database.SessionLocal()

def diagnose_ticker(ticker):
    print(f"\n--- Diagnosing {ticker} ---")
    
    # 1. Check Historical Data
    print("Checking StockData (History)...")
    history = db.query(models.StockData).filter(models.StockData.ticker == ticker).order_by(models.StockData.date.desc()).limit(5).all()
    if not history:
        print("  [ALERT] No history found!")
    else:
        for row in history:
            print(f"  {row.date}: Close={row.close}")
        max_date = history[0].date
        print(f"  Max Date: {max_date}")
        
        # Simulation Logic
        print("Running Fetch Logic Simulation:")
        today = date.today()
        should_fetch = True
        if max_date:
            if max_date >= today or max_date >= (today - timedelta(days=1)):
                print(f"  [DECISION] Data is up-to-date. Should Fetch = False")
                should_fetch = False
            else:
                 print(f"  [DECISION] Data is OLD. Should Fetch = True")
        
    # 2. Check Intraday Cache
    print("\nChecking CurrentDayCandle (Live Cache)...")
    today_candle = db.query(models.CurrentDayCandle).filter(models.CurrentDayCandle.ticker == ticker).first()
    if today_candle:
        print(f"  Found Candle: Date={today_candle.trading_date}, Price={today_candle.current_price}, Finalized={today_candle.is_finalized}")
    else:
        print("  [INFO] No intraday candle found.")
        
    # 3. Test Scraper (Imports check)
    try:
        from backend.main import scrape_google_finance_data
        print(f"\nTesting Scraper for {ticker}...")
        data = scrape_google_finance_data(ticker)
        print(f"  Result: {data}")
    except Exception as e:
        print(f"  [ERROR] Scraper failed: {e}")

if __name__ == "__main__":
    try:
        diagnose_ticker("INFY.NS")
        diagnose_ticker("^NSEI")
    finally:
        db.close()
