import requests
from bs4 import BeautifulSoup

def inspect_google_finance(ticker: str):
    symbol = ticker
    exchange = "NSE"
    if "." in ticker:
        parts = ticker.split(".")
        symbol = parts[0]
        if parts[1] == "NS": exchange = "NSE"
    
    url = f"https://www.google.com/finance/quote/{symbol}:{exchange}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    print(f"Fetching {url}...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all text elements to verify labels
        text = soup.get_text()
        print(f"Page Title: {soup.title.string}")
        
        # Search for "Range" (Day's Range)
        range_label = soup.find(string=lambda t: t and "Range" in t)
        if range_label:
            print(f"\nFound 'Range' label: {range_label}")
            parent = range_label.parent
            print(f"Parent: {parent.name} class={parent.get('class')}")
            # Try to find value nearby
            # Usually parent parent -> find P6K39c
            value_div = parent.find_next("div", class_="P6K39c")
            if value_div:
                print(f"Potential Range Value: {value_div.text}")
        
        # Search for "Open"
        open_label = soup.find(string=lambda t: t and "Open" in t)
        if open_label:
             print(f"\nFound 'Open' label: {open_label}")
             parent = open_label.parent
             print(f"Parent: {parent.name} class={parent.get('class')}")
             value_div = parent.find_next("div", class_="P6K39c")
             if value_div:
                print(f"Potential Open Value: {value_div.text}")

        # Dump all P6K39c values to see what we have
        print("\nAll values with class 'P6K39c':")
        values = soup.find_all("div", class_="P6K39c")
        for i, v in enumerate(values):
            print(f"{i}: {v.text}")

            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_google_finance("INFY.NS")
