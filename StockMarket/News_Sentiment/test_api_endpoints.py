"""
Quick test to verify all API endpoints are accessible
"""
import requests

BASE_URL = "http://localhost:8000"

print("Testing API Endpoints...\n")

# Test 1: Stocks list
print("1. Testing /api/stocks/list")
try:
    response = requests.get(f"{BASE_URL}/api/stocks/list?limit=5")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ Got {len(data.get('stocks', []))} stocks")
    else:
        print(f"   ✗ Error: {response.text[:100]}")
except Exception as e:
    print(f"   ✗ Error: {e}")

print()

# Test 2: ScanX news all
print("2. Testing /api/scanx/news/all")
try:
    response = requests.get(f"{BASE_URL}/api/scanx/news/all?limit=5")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ Got {len(data)} articles")
    else:
        print(f"   ✗ Error: {response.text[:100]}")
except Exception as e:
    print(f"   ✗ Error: {e}")

print()

# Test 3: ScanX full scraping (this will be slow!)
print("3. Testing /api/scanx/news/full/all (this takes 20-30 seconds)")
try:
    response = requests.get(f"{BASE_URL}/api/scanx/news/full/all?limit=5", timeout=60)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ Got {len(data)} articles from Playwright scraper!")
    else:
        print(f"   ✗ Error: {response.text[:100]}")
except Exception as e:
    print(f"   ✗ Error: {e}")

print("\n✅ All endpoint tests complete!")
