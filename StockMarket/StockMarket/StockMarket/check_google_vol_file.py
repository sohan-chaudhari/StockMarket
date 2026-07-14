
import requests
from bs4 import BeautifulSoup

def get_google_vol(ticker, f):
    url = f"https://www.google.com/finance/quote/{ticker}"
    f.write(f"\nChecking {url}...\n")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        found = False
        key_stats_rows = soup.select("div.gyFHrc")
        for row in key_stats_rows:
             label = row.select_one("div.mfs7Fc")
             if label and "Volume" in label.text:
                 val = row.select_one("div.P6K39c")
                 if val:
                     f.write(f"FOUND VOLUME: {val.text}\n")
                     found = True
                     break
        
        if not found:
            f.write("Volume label NOT found.\n")

    except Exception as e:
        f.write(f"Error: {e}\n")

with open("google_vol.txt", "w") as f_out:
    get_google_vol("NIFTY_BANK:INDEXNSE", f_out)
    get_google_vol("SENSEX:INDEXBOM", f_out)
