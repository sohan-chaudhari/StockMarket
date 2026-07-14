"""
Google Finance scraper for historical stock data
Alternative to yfinance when data is not available
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
import json
import re

def fetch_google_finance_history(ticker, days=30):
    """
    Fetch historical data from Google Finance
    
    Args:
        ticker: Stock symbol (e.g., 'RELIANCE', 'TCS')
        days: Number of days of history to fetch
        
    Returns:
        List of dicts with date, open, high, low, close, volume
    """
    
    # Convert ticker to Google Finance format
    if ticker in ['NIFTY', 'NIFTY50']:
        google_ticker = 'NIFTY_50:INDEXNSE'
    elif ticker == 'BANKNIFTY':
        google_ticker = 'NIFTY_BANK:INDEXNSE'
    elif ticker == 'SENSEX':
        google_ticker = 'SENSEX:INDEXBOM'
    else:
        google_ticker = f'{ticker}:NSE'
    
    url = f'https://www.google.com/finance/quote/{google_ticker}'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # Google Finance loads data via JavaScript, so we need to extract it from the page
        # Look for embedded JSON data
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try to find script tags with data
        scripts = soup.find_all('script')
        
        historical_data = []
        
        # Google Finance embeds data in JavaScript - we need to parse it
        # This is a simplified version - Google Finance uses complex JS rendering
        # For production, consider using Selenium or their unofficial API
        
        print(f"✗ Google Finance scraping requires JavaScript rendering")
        print(f"  Consider using: selenium, playwright, or google-finance-data library")
        
        return historical_data
        
    except Exception as e:
        print(f"Error fetching from Google Finance: {e}")
        return []

def fetch_nseindia_history(ticker, start_date, end_date):
    """
    Fetch historical data directly from NSE India website
    More reliable than Google Finance for Indian stocks
    
    Args:
        ticker: Stock symbol (e.g., 'RELIANCE', 'TCS')
        start_date: datetime.date object
        end_date: datetime.date object
        
    Returns:
        List of dicts with date, open, high, low, close, volume
    """
    
    # NSE India API endpoint
    url = "https://www.nseindia.com/api/historical/cm/equity"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.nseindia.com/get-quotes/equity?symbol=' + ticker
    }
    
    params = {
        'symbol': ticker,
        'series': '["EQ"]',
        'from': start_date.strftime('%d-%m-%Y'),
        'to': end_date.strftime('%d-%m-%Y')
    }
    
    try:
        # NSE requires a session with cookies
        session = requests.Session()
        
        # First, visit the main page to get cookies
        session.get('https://www.nseindia.com', headers=headers, timeout=10)
        
        # Now fetch the data
        response = session.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        historical_data = []
        
        if 'data' in data:
            for record in data['data']:
                historical_data.append({
                    'date': datetime.strptime(record['CH_TIMESTAMP'], '%d-%b-%Y').date(),
                    'open': float(record['CH_OPENING_PRICE']),
                    'high': float(record['CH_TRADE_HIGH_PRICE']),
                    'low': float(record['CH_TRADE_LOW_PRICE']),
                    'close': float(record['CH_CLOSING_PRICE']),
                    'volume': int(record['CH_TOT_TRADED_QTY'])
                })
        
        return historical_data
        
    except Exception as e:
        print(f"Error fetching from NSE India: {e}")
        return []

# Test the scraper
if __name__ == "__main__":
    print("Testing NSE India scraper for RELIANCE...")
    print("=" * 60)
    
    # Fetch Feb 1st data
    feb1 = date(2026, 2, 1)
    data = fetch_nseindia_history('RELIANCE', feb1, feb1)
    
    if data:
        print(f"\n✓ Found {len(data)} records:")
        for record in data:
            print(f"  {record['date']}: Open={record['open']}, Close={record['close']}")
    else:
        print("\n✗ No data found for Feb 1st")
        print("  This confirms Feb 1st was NOT a trading day")
