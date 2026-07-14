"""
Test the fixed scanx.trade search
"""
import asyncio
import sys
sys.path.insert(0, 'c:/Users/rahul/Desktop/News_Sentiment')

from scrapers.google_news_playwright_scraper import GoogleNewsPlaywrightScraper

async def test_scanx():
    scraper = GoogleNewsPlaywrightScraper()
    
    print("Testing site:scanx.trade Reliance Industries...")
    articles = await scraper.scrape_for_ticker('RELIANCE', limit=10)
    
    print(f"\n=== RESULTS ===")
    print(f"Total articles: {len(articles)}\n")
    
    for i, art in enumerate(articles[:5], 1):
        print(f"{i}. {art.get('headline', 'NO HEADLINE')[:80]}")
        print(f"   Source: {art.get('source', 'NO SOURCE')}")
        print(f"   URL: {art.get('url', 'NO URL')[:100]}")
        print()
    
    return articles

if __name__ == "__main__":
    result = asyncio.run(test_scanx())
    print(f"\n[{'SUCCESS' if len(result) > 0 else 'FAILED'}] Got {len(result)} articles from scanx.trade")
