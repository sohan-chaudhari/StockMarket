import sys
sys.path.insert(0, r'c:\Users\rahul\Desktop\News_Sentiment')

from scrapers.scanx_google_scraper import ScanXGoogleScraper

# Test the ScanX scraper with multiple tickers
scraper = ScanXGoogleScraper()

test_tickers = ['RELIANCE', 'TCS', 'INFY', 'ICICIBANK', 'HDFCBANK']

print("\n" + "="*70)
print("Testing ScanX News Scraper with Multiple Stocks")
print("="*70)

for ticker in test_tickers:
    company_name = scraper.get_company_name(ticker)
    print(f"\n{ticker} ({company_name}):")
    print(f"  Query: site:scanx.trade ({ticker} OR \"{company_name}\")")
    
    # Fetch articles
    articles = scraper.fetch_scanx_news_for_ticker(ticker, limit=3, days=10)
    
    print(f"  Found: {len(articles)} articles")
    
    if articles:
        for i, article in enumerate(articles[:2], 1):  # Show first 2
            print(f"    {i}. {article['title'][:70]}...")

print("\n" + "="*70)
print("Summary:")
print("If no articles are found, this could mean:")
print("1. scanx.trade doesn't have recent articles for these stocks")
print("2. Google News hasn't indexed scanx.trade articles yet")
print("3. The site:scanx.trade query doesn't return results")
print("="*70)
