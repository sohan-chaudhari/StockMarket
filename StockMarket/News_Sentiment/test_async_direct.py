"""
Test with exact same URL as the working test
"""
import asyncio
import sys
sys.path.insert(0, 'c:/Users/rahul/Desktop/News_Sentiment')

from scrapers.google_news_playwright_scraper import GoogleNewsPlaywrightScraper

async def test_specific_url():
    scraper = GoogleNewsPlaywrightScraper()
    
    # Use a simple query that we know works
    ticker = "RELIANCE"
    articles = await scraper.scrape_for_ticker(ticker, limit=5)
    
    print(f"\nGot {len(articles)} articles")
    for i, art in enumerate(articles[:3], 1):
        print(f"{i}. {art.get('headline', 'NO HEADLINE')[:60]}")
    
    return articles

if __name__ == "__main__":
    articles = asyncio.run(test_specific_url())
    if len(articles) > 0:
        print(f"\n[SUCCESS] Got {len(articles)} total articles")
    else:
        print("\n[FAILED] Got 0 articles")
