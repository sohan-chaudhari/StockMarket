import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

scraper = ScanXGoogleScraper()

print(f"\n{'='*80}")
print(f"Testing ALL STOCKS news")
print(f"{'='*80}\n")

articles = scraper.fetch_all_scanx_news(limit=20, days=30)

print(f'\nFound {len(articles)} articles for ALL STOCKS:\n')

for i, article in enumerate(articles[:15], 1):
    title = article['title']
    pub_date = article['published_at'].strftime('%b %d %H:%M')
    print(f"{i:2}. [{pub_date}] {title}")

print(f"\n{'='*80}")
print("Expected from Google News (Jan 1, 2026 9:00 AM):")
print("  1. MCX Stock Split: Last Day to Buy Shares...")
print("  2. India's Broadband Subscriber Base Crosses 100 Crore...")
print("  3. X Prepares to Boost Creator Payouts...")
print("  4. Tata Steel Completes ₹1,100 Crore Acquisition...")
print(f"{'='*80}\n")
