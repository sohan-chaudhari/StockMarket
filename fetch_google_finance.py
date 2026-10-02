"""
Fetch Feb 1st, 2026 data from Google Finance
Google Finance should have the Budget Day special session data
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from backend.database import SessionLocal
from backend.models import StockData, StockMetadata
from datetime import date, datetime, timedelta
import requests
from bs4 import BeautifulSoup
import time
import re

def get_google_finance_data(ticker, days_back=7):
    """
    Fetch historical data from Google Finance
    Returns list of candles for the past N days
    """
    
    # Convert to Google Finance format
    if ticker == 'NIFTY':
        symbol = 'NIFTY_50:INDEXNSE'
    elif ticker == 'BANKNIFTY':
        symbol = 'NIFTY_BANK:INDEXNSE'
    elif ticker == 'SENSEX':
        symbol = 'SENSEX:INDEXBOM'
    else:
        symbol = f'{ticker}:NSE'
    
    url = f'https://www.google.com/finance/quote/{symbol}'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Google Finance embeds data in the page
        # Look for the current price first
        price_div = soup.find('div', {'class': 'YMlKec fxKbKc'})
        
        if price_div:
            current_price = float(price_div.text.replace('₹', '').replace(',', '').strip())
            print(f"  Current price: ₹{current_price}")
            
            # For now, return just today's data
            # Google Finance historical data requires more complex scraping
            return [{
                'date': date.today(),
                'close': current_price,
                'open': current_price,  # Approximate
                'high': current_price,  # Approximate
                'low': current_price,   # Approximate
                'volume': 0
            }]
        
        return []
        
    except Exception as e:
        print(f"  Error: {e}")
        return []

def fetch_feb1_from_investing_com(ticker):
    """
    Alternative: Fetch from Investing.com which has reliable historical data
    """
    # Investing.com has historical data but requires more complex scraping
    # For now, return empty
    return None

def main():
    """Fetch Feb 1st data for top stocks"""
    
    print("Fetching Feb 1st, 2026 data from Google Finance...")
    print("=" * 60)
    
    # Top stocks to fetch
    top_stocks = [
        'NIFTY', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK',
        'BHARTIARTL', 'ITC', 'SBIN', 'LT', 'HINDUNILVR', 'KOTAKBANK'
    ]
    
    db = SessionLocal()
    feb1 = date(2026, 2, 1)
    records = []
    
    for ticker in top_stocks:
        print(f"\n{ticker}...", end=" ")
        
        data = get_google_finance_data(ticker)
        
        if data:
            # We got today's data, but we need Feb 1st
            # Google Finance doesn't easily provide historical data via scraping
            print("✗ Google Finance scraping limited to current price")
        else:
            print("✗ No data")
        
        time.sleep(0.5)  # Rate limiting
    
    print("\n" + "=" * 60)
    print("\n⚠ Google Finance Limitation:")
    print("  Google Finance requires JavaScript rendering for historical data")
    print("  Current price scraping works, but historical data needs:")
    print("    1. Selenium/Playwright for JS rendering")
    print("    2. Or use their unofficial API")
    print("    3. Or manual data entry from TradingView")
    
    print("\n💡 Recommended Solution:")
    print("  Please provide Feb 1st OHLC values from TradingView for RELIANCE")
    print("  I'll then add them to the database manually")
    
    db.close()

if __name__ == "__main__":
    main()
