
import requests
import sys

def check_range():
    url = "http://localhost:8001/api/stock-data/range?ticker=RELIANCE.NS&range=ALL"
    print(f"Fetching {url}...")
    try:
        r = requests.get(url)
        if r.status_code != 200:
             print(f"FAIL: Status {r.status_code}")
             return
        
        data = r.json()
        print(f"Count: {len(data)}")
        if len(data) == 0:
            print("FAIL: Responses is empty list []")
        else:
            print(f"First: {data[0]['date']}")
            print(f"Last: {data[-1]['date']}")
            print("SUCCESS: Data present.")
            
    except Exception as e:
        print(f"Error: {e}")

check_range()
