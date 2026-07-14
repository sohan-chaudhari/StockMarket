"""
Test the synchronous wrapper directly (as the API uses it)
"""
import sys
sys.path.insert(0, 'c:/Users/rahul/Desktop/News_Sentiment')

from scrapers.google_news_playwright_scraper import scrape_ticker_sync, GoogleNewsPlaywrightScraper

print("Testing synchronous wrapper for ticker RELIANCE...")
articles = scrape_ticker_sync("RELIANCE", limit=5)

print(f"\n=== RESULTS ===")
print(f"Total articles: {len(articles)}\n")

for i, article in enumerate(articles, 1):
    print(f"{i}. {article.get('headline', 'NO HEADLINE')[:80]}")
    print(f"   Ticker: {article.get('ticker', 'UNKNOWN')}")
    print(f"   URL: {article.get('url', 'NO URL')[:80]}")
    print()

if len(articles) > 0:
    print(f"[SUCCESS] Got {len(articles)} articles!")
else:
    print("[FAILED] Got 0 articles!")
