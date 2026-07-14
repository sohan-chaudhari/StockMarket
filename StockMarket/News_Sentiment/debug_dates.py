"""
Debug date parsing in RSS feed
"""
import feedparser
from urllib.parse import quote_plus
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ticker = "RELIANCE"
company_name = "Reliance Industries"

query = f'site:scanx.trade ({ticker} OR "{company_name}")'
encoded_query = quote_plus(query)
rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"

print(f"\n{'='*80}")
print(f"DEBUG: Date Parsing for RELIANCE articles")
print(f"{'='*80}\n")

feed = feedparser.parse(rss_url)
cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)

print(f"Cutoff date: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"Total entries: {len(feed.entries)}\n")

kept = 0
filtered_date = 0
filtered_source = 0

for idx, entry in enumerate(feed.entries[:20], 1):
    print(f"\n[Entry {idx}]")
    
    title = entry.title if hasattr(entry, 'title') else 'NO TITLE'
    print(f"  Title: {title[:70]}...")
    
    # Check if it's scanx
    is_scanx = False
    if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
        source_title = entry.source.title.lower()
        is_scanx = 'scanx.trade' in source_title or 'scanx' in source_title
    if not is_scanx and hasattr(entry, 'title'):
        is_scanx = 'scanx.trade' in entry.title.lower()
    
    if not is_scanx:
        print(f"  [FILTERED - Not scanx.trade]")
        filtered_source += 1
        continue
    
    # Parse date
    pub_str = entry.published if hasattr(entry, 'published') else None
    print(f"  Published string: {pub_str}")
    
    if pub_str:
        try:
            pub_date = parsedate_to_datetime(pub_str)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            print(f"  Parsed date: {pub_date.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            
            # Check if within range
            if pub_date < cutoff_date:
                days_old = (datetime.now(timezone.utc) - pub_date).days
                print(f"  [FILTERED - Too old: {days_old} days]")
                filtered_date += 1
                continue
            else:
                print(f"  [KEPT]")
                kept += 1
        except Exception as e:
            print(f"  Date parse error: {e}")
            print(f"  [KEPT - using current date as fallback]")
            kept += 1
    else:
        print(f"  No published date")
        print(f"  [KEPT - using current date as fallback]")
        kept += 1

print(f"\n{'='*80}")
print(f"Summary:")
print(f"  Kept: {kept}")
print(f"  Filtered (not scanx): {filtered_source}")
print(f"  Filtered (too old): {filtered_date}")
print(f"{'='*80}\n")
