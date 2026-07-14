"""
Direct test of the Playwright scraper to see the actual error
"""
import asyncio
import sys
sys.path.insert(0, 'c:/Users/rahul/Desktop/News_Sentiment')

from scrapers.google_news_playwright_scraper import GoogleNewsPlaywrightScraper

async def test_scraper():
    print("Testing Playwright scraper...")
    try:
        scraper = GoogleNewsPlaywrightScraper()
        print("[OK] Scraper initialized")
        
        print("\\nFetching all news (limit=5)...")
        articles = await scraper.scrape_all_news(limit=5)
        
        print(f"\\n[OK] Got {len(articles)} articles:")
        for i, article in enumerate(articles[:3], 1):
            print(f"  {i}. {article.get('headline', 'No headline')[:60]}")
        
        return articles
    except Exception as e:
        print(f"\\n[ERROR] Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    articles = asyncio.run(test_scraper())
    if articles:
        print(f"\\n[SUCCESS] Test successful! Got {len(articles)} articles")
    else:
        print("\\n[FAILED] Test failed - see error above")
