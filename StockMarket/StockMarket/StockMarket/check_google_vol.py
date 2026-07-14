
import requests
from bs4 import BeautifulSoup

def get_google_vol(ticker):
    url = f"https://www.google.com/finance/quote/{ticker}"
    print(f"Checking {url}...")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Look for "Volume" label
        vol_label = soup.find("div", string="Volume")
        if vol_label:
            parent = vol_label.parent
            val = parent.find("div", class_="P6K39c")
            if val:
                print(f"Found Volume: {val.text}")
                return
        
        # Robust check
        key_stats_rows = soup.select("div.gyFHrc")
        for row in key_stats_rows:
             label = row.select_one("div.mfs7Fc")
             if label and "Volume" in label.text:
                 val = row.select_one("div.P6K39c")
                 print(f"Robust Volume Search: {val.text if val else 'None'}")
                 
    except Exception as e:
        print(f"Error: {e}")

get_google_vol("NIFTY_BANK:INDEXNSE")
get_google_vol("SENSEX:INDEXBOM")
