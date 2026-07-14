
import sys
import os
import requests
from bs4 import BeautifulSoup
import traceback

# Ensure backend module can be found
current_dir = os.path.dirname(os.path.abspath(__file__)) # e:\StockMarket
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

def parse_price(text: str):
    if not text: return None
    clean = "".join([c for c in text if c.isdigit() or c == '.'])
    try:
        return float(clean)
    except:
        return None

def test_scrape(ticker: str):
    print(f"\n--- Testing Scraper for {ticker} ---")
    url = f"https://www.google.com/finance/quote/{ticker}:NSE"
    if "INDEX" in ticker:
         # Map NIFTY to Google format if needed, but here we assume raw or mapped
         pass
         
    # Manual Map logic for test
    if ticker == "NIFTY": url = "https://www.google.com/finance/quote/NIFTY_50:INDEXNSE"
    if ticker == "INFY.NS": url = "https://www.google.com/finance/quote/INFY:NSE"
    
    print(f"URL: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        print(f"Status Code: {response.status_code}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Price
        price_div = soup.find("div", class_="YMlKec fxKbKc")
        print(f"Price Div found: {price_div}")
        if price_div:
            print(f"Price Text: {price_div.text}")
            
        # 2. Key Stats
        key_stats_rows = soup.select("div.gyFHrc")
        print(f"Key Stats Rows found: {len(key_stats_rows)}")
        for row in key_stats_rows:
            label_div = row.select_one("div.mfs7Fc")
            value_div = row.select_one("div.P6K39c")
            if label_div and value_div:
                print(f"Stat: {label_div.get_text(strip=True)} -> {value_div.get_text(strip=True)}")
                
    except Exception as e:
        traceback.print_exc()

if __name__ == "__main__":
    test_scrape("INFY.NS")
    test_scrape("NIFTY")
