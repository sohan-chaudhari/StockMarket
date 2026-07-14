"""
Test ScanX scraper specifically for RELIANCE to debug missing articles
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from scrapers.scanx_google_scraper import ScanXGoogleScraper

# Initialize scraper
scraper = ScanXGoogleScraper()

# Test RELIANCE
ticker = "RELIANCE"
company_name = scraper.get_company_name(ticker)

print("\n" + "="*70)
print(f"Testing ScanX News Scraper for {ticker}")
print("="*70)
print(f"\nCompany Name: {company_name}")

# Build the query that's actually used
query = f'site:scanx.trade ({ticker} OR "{company_name}")'
print(f"Search Query: {query}")

# Fetch articles with debug info
articles = scraper.fetch_scanx_news_for_ticker(ticker, limit=50, days=7)

print(f"\n[OK] Found {len(articles)} articles for {ticker}:\n")

for i, article in enumerate(articles, 1):
    pub_date = article['published_at'].strftime('%Y-%m-%d %H:%M')
    print(f"{i}. [{pub_date}] {article['title'][:100]}")
    
print("\n" + "="*70)
