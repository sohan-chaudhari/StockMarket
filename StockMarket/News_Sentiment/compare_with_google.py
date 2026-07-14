"""
Compare our scraper results with what Google News actually shows
This will help identify which articles are missing
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

ticker = "RELIANCE"
scraper = ScanXGoogleScraper()

print(f"\n{'='*80}")
print(f"RELIANCE News from Our Scraper (First 15 articles)")
print(f"{'='*80}\n")

articles = scraper.fetch_scanx_news_for_ticker(ticker, limit=100, days=30)

print(f"Total articles found: {len(articles)}\n")

for i, article in enumerate(articles[:15], 1):
    pub_date = article['published_at'].strftime('%b %d')
    title = article['title']
    
    # Truncate title if too long
    if len(title) > 90:
        title = title[:87] + "..."
    
    print(f"{i:2}. [{pub_date}] {title}")

print(f"\n{'='*80}")
print(f"INSTRUCTIONS:")
print(f"1. Open Google News in browser")
print(f"2. Search: site:scanx.trade RELIANCE")
print(f"3. Compare the results above with what you see")
print(f"4. Tell me which articles are missing")
print(f"{'='*80}\n")
