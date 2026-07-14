import requests

# Test the fixed API endpoint
base_url = "http://localhost:8000"

stocks = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "NIFTY"]

print("Testing API with ticker parameter...\n")

for ticker in stocks:
    try:
        response = requests.get(f"{base_url}/api/stock-data/range?ticker={ticker}&range=1D")
        if response.status_code == 200:
            data = response.json()
            if data:
                today = data[-1]
                print(f"✓ {ticker}:")
                print(f"  Date: {today.get('date')}")
                print(f"  O: {today.get('open'):.2f}, H: {today.get('high'):.2f}, L: {today.get('low'):.2f}, C: {today.get('close'):.2f}")
            else:
                print(f"✗ {ticker}: No data")
        else:
            print(f"✗ {ticker}: HTTP {response.status_code}")
    except Exception as e:
        print(f"✗ {ticker}: {e}")
    print()
