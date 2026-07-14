
import requests
import json

def test_fetch():
    print("Testing /fetch-stock for Reliance...")
    try:
        r = requests.post("http://localhost:8001/api/fetch-stock", json={"ticker": "RELIANCE.NS"})
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_fetch()
