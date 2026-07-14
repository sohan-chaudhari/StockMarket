
import requests
from bs4 import BeautifulSoup

url = "https://www.google.com/finance/quote/NIFTY_BANK:INDEXNSE"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

try:
    print(f"Fetching {url}...")
    r = requests.get(url, headers=headers)
    soup = BeautifulSoup(r.text, 'html.parser')
    
    # Get all text
    text = soup.get_text(separator=' ', strip=True)
    
    with open("google_dump.txt", "w", encoding="utf-8") as f:
        f.write(text)
        
    print("Dumped to google_dump.txt")
    
    # Search for keywords
    if "Volume" in text:
        print("Found 'Volume' in text!")
        # Try to find snippet
        idx = text.find("Volume")
        print(f"Snippet: {text[idx:idx+50]}")
    else:
        print("'Volume' NOT found in text.")

except Exception as e:
    print(f"Error: {e}")
