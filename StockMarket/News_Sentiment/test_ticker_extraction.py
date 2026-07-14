import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

scraper = ScanXGoogleScraper()

# Test ALL stocks news
print("Testing ALL stocks news...")
articles = scraper.fetch_fresh_news(ticker="ALL", limit=5)

print(f"\nFound {len(articles)} articles:\n")

for i, article in enumerate(articles, 1):
    ticker = article['ticker']
    title = article['title'][:60]
    logo = article.get('logo_url', 'NO LOGO')[:50]
    
    print(f"{i}. Ticker: {ticker:12} Logo: {logo if logo else 'None'}")
    print(f"   Title: {title}")
    print()
