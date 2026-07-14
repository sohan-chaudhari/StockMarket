import requests

# Test the /api/all-stocks endpoint
url = "http://127.0.0.1:8000/api/all-stocks"

try:
    response = requests.get(url)
    if response.status_code == 200:
        stocks = response.json()
        
        # Check for indices
        indices = [s for s in stocks if s['ticker'] in ['NIFTY', 'BANKNIFTY', 'SENSEX']]
        
        print(f"Total stocks: {len(stocks)}")
        print(f"\nIndices found: {len(indices)}")
        
        for idx in indices:
            print(f"  ✓ {idx['ticker']:12} - {idx['name']}")
        
        if len(indices) == 3:
            print("\n✅ All indices are now in the search database!")
        else:
            print(f"\n⚠️ Only {len(indices)}/3 indices found")
    else:
        print(f"Error: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")
