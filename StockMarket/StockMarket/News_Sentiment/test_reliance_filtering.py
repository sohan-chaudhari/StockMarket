import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

scraper = ScanXGoogleScraper()

print(f"\n{'='*80}")
print(f"Testing RELIANCE news filtering")
print(f"{'='*80}\n")

articles = scraper.fetch_scanx_news_for_ticker('RELIANCE', limit=10, days=30)

print(f'\nFiltered Results ({len(articles)} articles):\n')

for i, article in enumerate(articles, 1):
    title = article['title']
    print(f"{i:2}. {title}")

print(f"\n{'='*80}")
print("Expected: Only articles where Reliance is the PRIMARY subject")
print("Should NOT include:")
print("  - 'India's Broadband...' (doesn't mention Reliance)")
print("  - 'Trishakti Industries... from Reliance' (about Trishakti)")
print("  - 'Jupiter Hospital, Reliance-Backed...' (about Jupiter)")
print(f"{'='*80}\n")
