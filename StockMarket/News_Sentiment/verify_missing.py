"""
Check if the missing articles appear in the RSS feed at all
"""
import feedparser
from urllib.parse import quote_plus

# Try the most specific query
query = 'site:scanx.trade "Reliance Industries"'
encoded_query = quote_plus(query)
rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"

print(f"\n{'='*80}")
print(f"Checking for Missing Articles in RSS Feed")
print(f"Query: {query}")
print(f"{'='*80}\n")

feed = feedparser.parse(rss_url)
print(f"Total entries: {len(feed.entries)}\n")

# Keywords from missing articles
missing_articles = [
    ("$30B Claim", "Reliance Industries Clarifies $30B Claim Reports"),
    ("India Claims $30 Billion", "India Claims $30 Billion From Reliance Industries"),
    ("Market Focus", "Market Focus: RIL, Waaree Energies"),
    ("Dhirubhai Ambani", "Pays Tribute to Founder Dhirubhai Ambani")
]

print("Searching for missing articles:\n")

for keyword, description in missing_articles:
    print(f"Searching: {description}")
    found = False
    
    for idx, entry in enumerate(feed.entries):
        title = entry.title if hasattr(entry, 'title') else ''
        
        if keyword.lower() in title.lower():
            pub_date = entry.published if hasattr(entry, 'published') else 'Unknown date'
            print(f"  ✓ FOUND at position #{idx+1}")
            print(f"    Title: {title}")
            print(f"    Published: {pub_date}")
            found = True
            break
    
    if not found:
        print(f"  ✗ NOT IN RSS FEED (but appears on Google News web)")
    print()

# Also show what IS in the top 10
print(f"\n{'='*80}")
print(f"Top 10 Articles Currently in RSS Feed:")
print(f"{'='*80}\n")

for idx, entry in enumerate(feed.entries[:10], 1):
    title = entry.title if hasattr(entry, 'title') else 'NO TITLE'
    pub_date = entry.published if hasattr(entry, 'published') else 'Unknown'
    print(f"{idx:2}. {title}")
    print(f"    Published: {pub_date}\n")
