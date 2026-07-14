import requests
from datetime import date

# Test the API endpoint that the frontend uses
base_url = "http://localhost:8000"
ticker = "RELIANCE.NS"

print(f"Testing API endpoint for {ticker}...")
print(f"Today: {date.today()}\n")

# Test 1: Get stock data with range
try:
    response = requests.get(f"{base_url}/api/stock-data/range?ticker={ticker}&range=1M")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ API Response successful - {len(data)} data points")
        
        # Get today's data (last item)
        if data:
            today_data = data[-1]
            print(f"\nToday's data from API:")
            print(f"  Date: {today_data.get('date')}")
            print(f"  Open: {today_data.get('open')}")
            print(f"  High: {today_data.get('high')}")
            print(f"  Low: {today_data.get('low')}")
            print(f"  Close: {today_data.get('close')}")
            print(f"  Volume: {today_data.get('volume')}")
            
            # Show last 3 days for context
            print(f"\nLast 3 days:")
            for item in data[-3:]:
                print(f"  {item.get('date')}: O={item.get('open'):.2f}, H={item.get('high'):.2f}, L={item.get('low'):.2f}, C={item.get('close'):.2f}")
    else:
        print(f"✗ API Error: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"✗ Request failed: {e}")
    print("\nIs the backend server running? Try: cd backend && uvicorn main:app --reload")
