"""
Debug script to see RAW Google News RSS entries for RELIANCE
This will help identify why articles are being filtered out
"""

import feedparser
from urllib.parse import quote_plus
from datetime import datetime, timedelta, timezone

ticker = "RELIANCE"
company_name = "Reliance Industries"

# Build query
query = f'site:scanx.trade ({ticker} OR "{company_name}")'
encoded_query = quote_plus(query)
rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"

print(f"\n{'='*80}")
print(f"DEBUG: Google News RSS for {ticker}")
print(f"{'='*80}")
print(f"Query: {query}")
print(f"URL: {rss_url}\n")

# Fetch feed
feed = feedparser.parse(rss_url)

print(f"Total entries in feed: {len(feed.entries)}\n")

# Calculate cutoff (7 days)
cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)

kept = 0
filtered = 0

# Check each entry
for idx, entry in enumerate(feed.entries[:30], 1):  # Check first 30
    print(f"\n[Entry {idx}]")
    
    # Title
    title = entry.title if hasattr(entry, 'title') else 'NO TITLE'
    print(f"  Title: {title[:80]}...")
    
    # URL
    url = entry.link if hasattr(entry, 'link') else 'NO URL'
    print(f"  URL: {url[:100]}...")
    
    # Source check
    has_source_attr = hasattr(entry, 'source')
    print(f"  Has source attribute: {has_source_attr}")
    
    if has_source_attr:
        has_title = hasattr(entry.source, 'title')
        print(f"  Has source.title: {has_title}")
        if has_title:
            source_title = entry.source.title
            print(f"  Source title: '{source_title}'")
            is_scanx_source = 'scanx.trade' in source_title.lower() or 'scanx' in source_title.lower()
            print(f"  Is scanx (source): {is_scanx_source}")
    
    # Title check
    is_scanx_title = 'scanx.trade' in title.lower() if title != 'NO TITLE' else False
    print(f"  Is scanx (title): {is_scanx_title}")
    
    # Final decision
    is_scanx = False
    if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
        source_title = entry.source.title.lower()
        is_scanx = 'scanx.trade' in source_title or 'scanx' in source_title
    if not is_scanx and hasattr(entry, 'title'):
        is_scanx = 'scanx.trade' in entry.title.lower()
    
    if is_scanx:
        print(f"  [KEPT]")
        kept += 1
    else:
        print(f"  [FILTERED]")
        filtered += 1

print(f"\n{'='*80}")
print(f"Summary: {kept} kept, {filtered} filtered out")
print(f"{'='*80}\n")
