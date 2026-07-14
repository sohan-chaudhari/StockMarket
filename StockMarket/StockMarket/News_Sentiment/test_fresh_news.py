import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

scraper = ScanXGoogleScraper()

print(f"\n{'='*80}")
print(f"Testing FRESH NEWS for ALL stocks")
print(f"{'='*80}\n")

articles = scraper.fetch_fresh_news(ticker="ALL", limit=20)

print(f'\nFinal Results:\n')

for i, article in enumerate(articles[:10], 1):
    title = article['title'][:80]
    pub_date = article['published_at'].strftime('%b %d %H:%M')
    print(f"{i:2}. [{pub_date}] {title}")

if articles:
    print(f"\n✓ Newest article: {articles[0]['published_at'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"✓ Total articles: {len(articles)}")
