import feedparser
from urllib.parse import quote_plus
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

def test_google_news_direct(query, limit=5):
    """Test Google News RSS directly"""
    base_url = "https://news.google.com/rss/search"
    encoded_query = quote_plus(query)
    rss_url = f"{base_url}?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
    
    print(f"\nQuery: {query}")
    print(f"URL: {rss_url}\n")
    
    feed = feedparser.parse(rss_url)
    
    print(f"Found {len(feed.entries)} total entries")
    
    for i, entry in enumerate(feed.entries[:limit], 1):
        print(f"\n{i}. {entry.title if hasattr(entry, 'title') else 'No title'}")
        print(f"   URL: {entry.link if hasattr(entry, 'link') else 'No link'}")
        if hasattr(entry, 'published'):
            print(f"   Published: {entry.published}")
        if hasattr(entry, 'source'):
            print(f"   Source: {entry.source.get('title', 'Unknown')}")

print("="*70)
print("Testing Google News RSS - Different Queries")
print("="*70)

# Test 1: With site:scanx.trade
test_google_news_direct('site:scanx.trade ICICIBANK OR "ICICI Bank"', limit=3)

# Test 2: Without site filter, just company name
test_google_news_direct('ICICIBANK OR "ICICI Bank"', limit=3)

# Test 3: Just site:scanx.trade
test_google_news_direct('site:scanx.trade', limit=5)

print("\n" + "="*70)
