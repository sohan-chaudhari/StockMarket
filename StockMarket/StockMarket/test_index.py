import requests
from bs4 import BeautifulSoup

def test_index_scrape():
    # Google Finance URL for Nifty 50
    # https://www.google.com/finance/quote/NIFTY_50:INDEXNSE
    url = "https://www.google.com/finance/quote/NIFTY_50:INDEXNSE"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    print(f"Testing URL: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Current Price
        price_div = soup.find("div", class_="YMlKec fxKbKc")
        print(f"Price: {price_div.text if price_div else 'Not Found'}")
        
        # 2. P6K39c stats
        stats = soup.find_all("div", class_="P6K39c")
        for i, s in enumerate(stats):
            print(f"Stat {i}: {s.text}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_index_scrape()
