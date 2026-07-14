import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

scraper = ScanXGoogleScraper()
articles = scraper.fetch_scanx_news_for_ticker('HDFCBANK', limit=20, days=30)

print(f'\n{"="*80}')
print(f'Found {len(articles)} articles for HDFCBANK')
print(f'{"="*80}\n')

for i, article in enumerate(articles[:15], 1):
    title = article['title'][:90]
    pub_date = article['published_at'].strftime('%b %d')
    print(f"{i:2}. [{pub_date}] {title}")
