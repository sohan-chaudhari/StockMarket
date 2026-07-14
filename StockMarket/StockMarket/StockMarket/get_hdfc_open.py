import requests
from bs4 import BeautifulSoup

def get_google_finance_url(ticker: str) -> str:
    if "." in ticker:
        symbol, exchange = ticker.split(".")
        if exchange == "NS": return f"https://www.google.com/finance/quote/{symbol}:NSE"
    return f"https://www.google.com/finance/quote/{ticker}:NSE"

def fetch_open():
    ticker = "HDFCBANK.NS"
    url = get_google_finance_url(ticker)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Open is usually index 0 in P6K39c class
        stats_values = soup.find_all("div", class_="P6K39c")
        if stats_values:
            open_price = stats_values[0].text.strip()
            print(open_price)
        else:
            print("Open not found")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_open()
