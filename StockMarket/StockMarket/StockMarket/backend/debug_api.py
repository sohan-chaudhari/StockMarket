import requests

try:
    print("Hitting http://127.0.0.1:8000/api/top-9-history ...")
    resp = requests.get("http://127.0.0.1:8000/api/top-9-history")
    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
        print("Keys:", list(data.keys()))
        for k, v in list(data.items())[:2]:
            print(f"{k}: {len(v)} points")
            if v:
                print(f"  First: {v[0]}")
                print(f"  Last:  {v[-1]}")
            else:
                print("  [EMPTY]")
    except Exception as e:
        print(f"JSON Decode Error: {resp.text}")

except Exception as e:
    print(f"Request Failed: {e}")
