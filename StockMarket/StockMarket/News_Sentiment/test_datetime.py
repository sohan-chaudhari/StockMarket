"""
Test to verify published_time is returning ISO datetime
"""
import asyncio
import json
import sys
sys.path.insert(0, 'c:/Users/rahul/Desktop/News_Sentiment')

from scrapers.google_news_playwright_scraper import GoogleNewsPlaywrightScraper

async def test_datetime():
    scraper = GoogleNewsPlaywrightScraper()
    
    articles = await scraper.scrape_for_ticker('RELIANCE', limit=3)
    
    print(f"Got {len(articles)} articles\n")
    
    for i, art in enumerate(articles, 1):
        print(f"{i}. {art.get('headline', 'NO HEADLINE')[:60]}")
        print(f"   Published: {art.get('published_time', 'NO TIME')}")
        print()

if __name__ == "__main__":
    asyncio.run(test_datetime())
