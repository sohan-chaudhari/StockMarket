import requests

# Check NIFTY data
url = "http://127.0.0.1:8000/api/top-9-history"

try:
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        
        if 'NIFTY' in data:
            print(f"NIFTY:")
            print(f"  Points: {len(data['NIFTY'])}")
            for point in data['NIFTY']:
                print(f"    {point['day']}: ₹{point['price']}")
        else:
            print("NIFTY: NOT IN RESPONSE")
    else:
        print(f"Error: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")
