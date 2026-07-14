import requests

# Check BANKNIFTY and SENSEX data
url = "http://127.0.0.1:8000/api/top-9-history"

try:
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        
        for ticker in ['BANKNIFTY', 'SENSEX']:
            if ticker in data:
                print(f"\n{ticker}:")
                print(f"  Points: {len(data[ticker])}")
                for point in data[ticker]:
                    print(f"    {point['day']}: ₹{point['price']}")
            else:
                print(f"\n{ticker}: NOT IN RESPONSE")
    else:
        print(f"Error: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")
