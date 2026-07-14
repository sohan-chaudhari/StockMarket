import requests
from bs4 import BeautifulSoup

def scrape_google_finance_price(ticker: str):
    symbol = ticker
    exchange = "NSE"
    if "." in ticker:
        parts = ticker.split(".")
        symbol = parts[0]
        if parts[1] == "NS": exchange = "NSE"
        elif parts[1] == "BO": exchange = "BOM"
    
    url = f"https://www.google.com/finance/quote/{symbol}:{exchange}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    print(f"Scraping URL: {url}")
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Current class for price in Google Finance (div.YMlKec.fxKbKc)
        price_div = soup.find("div", class_="YMlKec fxKbKc")
        if price_div:
            # Remove currency symbol and commas
            price_text = price_div.text.replace("₹", "").replace(",", "").strip()
            print(f"Found Price: {price_text}")
            return float(price_text)
        else:
            print("Could not find price element. Dumping partial HTML:")
            print(soup.prettify()[:1000]) # Print first 1000 chars
            return None
    except Exception as e:
        print(f"Scraping error: {e}")
        return None

if __name__ == "__main__":
    scrape_google_finance_price("INFY.NS")
