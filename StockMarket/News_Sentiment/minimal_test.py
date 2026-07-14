"""
Minimal reproduction - use the EXACT scraper class instance
"""
import asyncio
from pathlib import Path

# Import the ACTUAL scraper class
import sys
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.google_news_playwright_scraper import GoogleNewsPlaywrightScraper

async def minimal_test():
    print("Creating scraper instance...")
    scraper = GoogleNewsPlaywrightScraper()
    
    print(f"Loaded {len(scraper.ticker_to_company)} stock mappings")
    
    print("\nTesting scrape_for_ticker('RELIANCE', limit=5)...")
    articles = await scraper.scrape_for_ticker('RELIANCE', limit=5)
    
    print(f"\n=== RESULTS ===")
    print(f"Total articles: {len(articles)}")
    
    for i, art in enumerate(articles[:5], 1):
        print(f"\n{i}. {art.get('headline', 'NO HEADLINE')}")
        print(f"   URL: {art.get('url', 'NO URL')[:80]}")
        print(f"   Source: {art.get('source', 'NO SOURCE')}")
    
    return articles

if __name__ == "__main__":
    result = asyncio.run(minimal_test())
    print(f"\n[{'SUCCESS' if len(result) > 0 else 'FAILED'}] Got {len(result)} articles")
