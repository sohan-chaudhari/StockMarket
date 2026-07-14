import requests
from bs4 import BeautifulSoup

def debug_scrape():
    ticker = "HDFCBANK.NS"
    url = f"https://www.google.com/finance/quote/{ticker.split('.')[0]}:NSE"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # helper to find label for a value
    # P6K39c is the value class. The label is usually in a sibling or parent's sibling.
    # Structure is often: <div class="gyFHrc"><div class="m58n0d">Label</div><div class="P6K39c">Value</div></div>
    
    # Remove scripts and styles
    for script in soup(["script", "style"]):
        script.decompose()

    with open("page_dump.html", "w", encoding="utf-8") as f:
        f.write(soup.prettify())
        
    print("Dumped cleaned HTML to page_dump.html")

if __name__ == "__main__":
    debug_scrape()
