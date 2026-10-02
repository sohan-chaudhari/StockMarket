import requests
from bs4 import BeautifulSoup

def get_google_finance_url(ticker: str) -> str:
    # Basic URL mapping logic from main.py
    if "." in ticker:
        symbol, exchange = ticker.split(".")
        if exchange == "NS": return f"https://www.google.com/finance/quote/{symbol}:NSE"
        if exchange == "BO": return f"https://www.google.com/finance/quote/{symbol}:BOM"
    return f"https://www.google.com/finance/quote/{ticker}:NSE"

def fetch_close():
    ticker = "ALKEM.NS"
    url = get_google_finance_url(ticker)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        price_div = soup.find("div", class_="YMlKec fxKbKc")
        if price_div:
            close_price = price_div.text.strip()
            # print(f"Raw Scrape: {price_div.text}")
            print(close_price) # User asked for "only the close print"
        else:
            print("Price not found")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_close()
