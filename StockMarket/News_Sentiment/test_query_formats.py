"""
Test different query formats for Google News RSS to see which gets the most articles
"""
import feedparser
from urllib.parse import quote_plus

ticker = "RELIANCE"
company_name = "Reliance Industries"

# Test different query formats
queries = [
    f'site:scanx.trade "{company_name}"',
    f'site:scanx.trade {ticker}',
    f'site:scanx.trade ({ticker} OR "{company_name}")',
    f'site:scanx.trade Reliance',
    f'"scanx.trade" "{company_name}"',
]

print(f"\n{'='*80}")
print(f"Testing Different Query Formats for Google News RSS")
print(f"{'='*80}\n")

best_query = None
best_count = 0

for idx, query in enumerate(queries, 1):
    encoded_query = quote_plus(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
    
    print(f"\n[Test {idx}] Query: {query}")
    print(f"URL: {rss_url[:100]}...")
    
    try:
        feed = feedparser.parse(rss_url)
        count = len(feed.entries)
        print(f"Result: {count} articles found")
        
        if count > 0:
            print(f"First 3 articles:")
            for i, entry in enumerate(feed.entries[:3], 1):
                title = entry.title if hasattr(entry, 'title') else 'NO TITLE'
                print(f"  {i}. {title[:70]}...")
        
        if count > best_count:
            best_count = count
            best_query = query
            
    except Exception as e:
        print(f"Error: {e}")

print(f"\n{'='*80}")
print(f"BEST QUERY: {best_query}")
print(f"Articles found: {best_count}")
print(f"{'='*80}\n")
