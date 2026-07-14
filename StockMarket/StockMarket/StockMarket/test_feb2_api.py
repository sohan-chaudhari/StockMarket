import requests
import json

# Test the /api/top-9-history endpoint
url = "http://127.0.0.1:8000/api/top-9-history"

try:
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        
        # Check NIFTY data
        if 'NIFTY' in data:
            print("NIFTY Historical Data:")
            for point in data['NIFTY']:
                print(f"  {point['day']}: ₹{point['price']}")
            
            # Check if Feb 2 is present
            feb2_present = any('Feb 2' in point['day'] for point in data['NIFTY'])
            print(f"\n✓ Feb 2 present: {feb2_present}")
        
        # Check RELIANCE data
        if 'RELIANCE' in data:
            print("\nRELIANCE Historical Data:")
            for point in data['RELIANCE']:
                print(f"  {point['day']}: ₹{point['price']}")
            
            feb2_present = any('Feb 2' in point['day'] for point in data['RELIANCE'])
            print(f"\n✓ Feb 2 present: {feb2_present}")
    else:
        print(f"Error: Status {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"Error: {e}")
