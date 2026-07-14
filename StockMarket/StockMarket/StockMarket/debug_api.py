import requests
import json

# Test the /api/top-9-history endpoint
url = "http://127.0.0.1:8000/api/top-9-history"

try:
    response = requests.get(url)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        
        print("\nAPI Response Summary:")
        for ticker, history in data.items():
            print(f"\n{ticker}:")
            print(f"  Points: {len(history)}")
            if history:
                for point in history:
                    print(f"    {point['day']}: ₹{point['price']}")
            else:
                print("  NO DATA!")
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
