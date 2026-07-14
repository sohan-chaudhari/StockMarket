"""
Test to see exactly what articles the API returns for RELIANCE
"""
import requests
import json
from datetime import datetime

API_BASE_URL = "http://localhost:8000/api/v1"
ticker = "RELIANCE"

# Test the API endpoint
url = f"{API_BASE_URL}/scanx/news/{ticker}?days=7&limit=50"

print(f"\n{'='*80}")
print(f"Testing API: {url}")
print(f"{'='*80}\n")

try:
    response = requests.get(url)
    
    if response.status_code == 200:
        articles = response.json()
        print(f"Got {len(articles)} articles\n")
        
        for i, article in enumerate(articles[:10], 1):
            pub_date = article['published_at']
            try:
                dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                formatted_date = dt.strftime('%Y-%m-%d %H:%M')
            except:
                formatted_date = pub_date
            
            print(f"{i}. [{formatted_date}]")
            print(f"   {article['title'][:100]}")
            print()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        
except Exception as e:
    print(f"Error: {e}")
    print("Note: Make sure the backend is running on port 8000")

print(f"{'='*80}\n")
