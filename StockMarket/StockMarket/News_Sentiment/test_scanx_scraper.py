import sys
sys.path.insert(0, r'c:\Users\rahul\Desktop\News_Sentiment')

from scrapers.scanx_google_scraper import ScanXGoogleScraper

# Test the ScanX scraper with ICICIBANK
scraper = ScanXGoogleScraper()

print("\n" + "="*70)
print("Testing ScanX News Scraper for ICICIBANK")
print("="*70)

# Get company name
company_name = scraper.get_company_name('ICICIBANK')
print(f"\nCompany Name: {company_name}")
print(f"Search Query: site:scanx.trade (ICICIBANK OR \"{company_name}\")")

# Fetch articles (increased to 15 days to find more results)
articles = scraper.fetch_scanx_news_for_ticker('ICICIBANK', limit=10, days=15)

print(f"\n[OK] Found {len(articles)} articles for ICICIBANK:\n")

for i, article in enumerate(articles, 1):
    print(f"{i}. {article['title']}")
    print(f"   URL: {article['url']}")
    print(f"   Published: {article['published_at']}")
    print(f"   Excerpt: {article['excerpt'][:100]}...")
    print()

# Verify all articles are from scanx.trade
all_scanx = all('scanx.trade' in art['url'].lower() for art in articles)
print(f"\n[{'OK' if all_scanx else 'FAIL'}] All articles are from scanx.trade: {all_scanx}")

# Check if titles mention ICICI or ICICI Bank
icici_keywords = ['icici', 'icicibank']
relevant = []
for art in articles:
    title_lower = art['title'].lower()
    if any(keyword in title_lower for keyword in icici_keywords):
        relevant.append(art)

print(f"[{'OK' if len(relevant) > 0 else 'WARN'}] {len(relevant)}/{len(articles)} articles mention ICICI in title")

print("\n" + "="*70)
