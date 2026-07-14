import requests
from bs4 import BeautifulSoup

def parse_price(text: str):
    if not text: return None
    clean = "".join([c for c in text if c.isdigit() or c == '.'])
    try:
        val = float(clean)
        return val
    except:
        return None

def test_scrape_logic(ticker: str):
    url = f"https://www.google.com/finance/quote/{ticker}:NSE"
    if "." in ticker:
        symbol, exchange = ticker.split(".")
        if exchange == "NS":
            url = f"https://www.google.com/finance/quote/{symbol}:NSE"
            
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    print(f"Scraping {url}...")
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        data = {}
        
        # 1. Current Price
        price_div = soup.find("div", class_="YMlKec fxKbKc")
        if price_div:
            data['current_price'] = parse_price(price_div.text)
            print(f"Found Price: {data['current_price']}")
        else:
            print("Price NOT FOUND")

        # Dump text to file
        with open("debug_output.txt", "w", encoding="utf-8") as f:
             f.write(soup.prettify())
        
        print(f"Dumped HTML to debug_output.txt ({len(soup.prettify())} bytes)")

    
        # --- 3. Parse Key Stats (New Logic) ---
        stats = {'open': None, 'high': None, 'low': None, 'previous_close': None}
        print("--> Parsing Key Stats sections (div.gyFHrc)...")
        key_stats_rows = soup.select("div.gyFHrc")
        for row in key_stats_rows:
            try:
                label_div = row.select_one("div.mfs7Fc")
                value_div = row.select_one("div.P6K39c")
                
                if label_div and value_div:
                    label = label_div.get_text(strip=True)
                    value = value_div.get_text(strip=True)
                    print(f"    Found row: '{label}' -> '{value}'")
                    
                    if "Previous close" in label:
                        stats['previous_close'] = parse_price(value)
                    elif "Day range" in label:
                        # Format: "Low - High" e.g., "₹978.70 - ₹987.00"
                        parts = value.split('-')
                        if len(parts) == 2:
                            stats['low'] = parse_price(parts[0])
                            stats['high'] = parse_price(parts[1])
                    elif "Year range" in label:
                         pass # Not needed currently
                    elif "Open" in label:
                        stats['open'] = parse_price(value)
            except Exception as e:
                print(f"Error parsing row: {e}")

        data.update(stats)
        
        print("\n--- Scraped Fields ---")
        print(f"Current: {data.get('current_price')}")
        print(f"Open: {data.get('open')}")
        print(f"High: {data.get('high')}")
        print(f"Low: {data.get('low')}")
        print(f"Prev Close: {data.get('prev_close')}")
        
        return data

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_scrape_logic("HDFCBANK.NS")
