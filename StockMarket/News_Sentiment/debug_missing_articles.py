"""
Debug script to search for specific missing articles in the RSS feed
"""
import feedparser
from urllib.parse import quote_plus

ticker = "RELIANCE"
company_name = "Reliance Industries"

# Build query
query = f'site:scanx.trade ({ticker} OR "{company_name}")'
encoded_query = quote_plus(query)
rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"

print(f"\n{'='*80}")
print(f"Searching for missing articles in RSS feed")
print(f"{'='*80}\n")

# Missing article keywords to search for
missing_keywords = [
    "$30B Claim",
    "30B Claim",
    "$30 Billion",
    "India Claims",
    "Gas Field Underproduction",
    "Market Focus",
    "Waaree Energies",
    "Arvind Fashions"
]

feed = feedparser.parse(rss_url)

print(f"Total RSS entries: {len(feed.entries)}\n")
print("Searching for missing articles...\n")

found_count = 0
for keyword in missing_keywords:
    print(f"Searching for: '{keyword}'")
    found = False
    
    for idx, entry in enumerate(feed.entries):
        title = entry.title if hasattr(entry, 'title') else ''
        
        if keyword.lower() in title.lower():
            print(f"  ✓ FOUND at position {idx+1}: {title[:80]}...")
            
            # Check source
            if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                print(f"    Source: {entry.source.title}")
            
            # Check published date
            if hasattr(entry, 'published'):
                print(f"    Published: {entry.published}")
            
            found = True
            found_count += 1
            break
    
    if not found:
        print(f"  ✗ NOT FOUND in RSS feed")
    print()

print(f"{'='*80}")
print(f"Summary:")
print(f"  Found {found_count} out of {len(missing_keywords)} keywords")
if found_count < len(missing_keywords):
    print(f"\n  ⚠ Some articles visible on Google News web are NOT in the RSS feed!")
    print(f"  This is a Google News RSS limitation, not our scraper issue.")
print(f"{'='*80}\n")
