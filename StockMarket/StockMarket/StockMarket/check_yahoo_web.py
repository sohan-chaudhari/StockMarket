
import requests
from bs4 import BeautifulSoup

def check_yahoo_web(ticker):
    url = f"https://finance.yahoo.com/quote/{ticker}"
    print(f"Checking {url}...")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            print(f"Failed: {r.status_code}")
            return
            
        soup = BeautifulSoup(r.text, 'html.parser')
        # Look for "Volume"
        # Strategy: Search for data-field="regularMarketVolume"
        
        # Yahoo often uses specific data-testid or classes
        # Try finding the label and the value
        
        # Generic text search
        text = soup.get_text()
        if "Volume" in text:
            print("Found 'Volume' keyword.")
            # This is hard to parse reliability from raw text without markers
            
        # Try specific selectors common in Yahoo
        # <td data-test="VOLUME-value">
        vol_val = soup.find("td", {"data-test": "TD_VOLUME-value"})
        if vol_val:
            print(f"MATCH data-test='TD_VOLUME-value': {vol_val.text}")
            return

        vol_val_2 = soup.find("fin-streamer", {"data-field": "regularMarketVolume"})
        if vol_val_2:
            print(f"MATCH fin-streamer regularMarketVolume: {vol_val_2.text} (value={vol_val_2.get('value')})")
            return
            
        print("No robust volume selector matched.")

    except Exception as e:
        print(f"Error: {e}")

check_yahoo_web("^NSEBANK")
