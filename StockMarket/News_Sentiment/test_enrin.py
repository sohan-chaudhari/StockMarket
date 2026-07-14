import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

scraper = ScanXGoogleScraper()

# Test ENRIN
ticker = "ENRIN"
company_name = scraper.ticker_to_company.get(ticker, "NOT FOUND")

print(f"\n{'='*80}")
print(f"Testing ticker: {ticker}")
print(f"Company name: {company_name}")
print(f"{'='*80}\n")

articles = scraper.fetch_scanx_news_for_ticker(ticker, limit=10, days=30)

print(f'\nFound {len(articles)} articles for {ticker}:\n')

for i, article in enumerate(articles[:10], 1):
    title = article['title']
    pub_date = article['published_at'].strftime('%b %d')
    print(f"{i:2}. [{pub_date}] {title}")
