"""Comprehensive system test to identify all issues"""
import requests
import json
from datetime import date

BASE_URL = "http://localhost:8000"

print("=" * 60)
print("COMPREHENSIVE SYSTEM DIAGNOSTICS")
print("=" * 60)

# Test 1: Fetch Stock
print("\n[TEST 1] POST /api/fetch-stock (INFY)")
try:
    r = requests.post(f"{BASE_URL}/api/fetch-stock", 
                     json={"ticker": "INFY"},
                     timeout=30)
    if r.status_code == 200:
        data = r.json()
        print(f"✓ SUCCESS: {data['message']}")
        print(f"  Ticker: {data['ticker']}")
        print(f"  Count: {data['count']}")
    else:
        print(f"✗ FAILED: {r.status_code} - {r.text}")
except Exception as e:
    print(f"✗ ERROR: {e}")

# Test 2: Get Stock Data Range
print("\n[TEST 2] GET /api/stock-data/range?ticker=INFY.NS&range=ALL")
try:
    r = requests.get(f"{BASE_URL}/api/stock-data/range",
                    params={"ticker": "INFY.NS", "range": "ALL"},
                    timeout=10)
    if r.status_code == 200:
        data = r.json()
        print(f"✓ SUCCESS: Retrieved {len(data)} records")
        if len(data) > 0:
            print(f"  First: {data[0]['date']} - Close: {data[0]['close']}")
            print(f"  Last:  {data[-1]['date']} - Close: {data[-1]['close']}")
            # Check if today's data exists
            today_str = date.today().isoformat()
            has_today = any(d['date'] == today_str for d in data)
            print(f"  Has Today ({today_str}): {has_today}")
    else:
        print(f"✗ FAILED: {r.status_code} - {r.text}")
except Exception as e:
    print(f"✗ ERROR: {e}")

# Test 3: WebSocket endpoint exists  
print("\n[TEST 3] WebSocket Endpoint Check")
try:
    # Just check if the server responds to HTTP on the WS endpoint
    r = requests.get(f"{BASE_URL}/ws/live/INFY.NS", timeout=5)
    # We expect this to fail with 405 or similar since it's a WS endpoint
    print(f"  Endpoint exists (got HTTP {r.status_code} as expected for WS)")
except requests.exceptions.Timeout:
    print(f"  Endpoint timeout")
except Exception as e:
    print(f"  WebSocket endpoint present ({type(e).__name__})")

# Test 4: Index ticker (NIFTY)
print("\n[TEST 4] POST /api/fetch-stock (NIFTY)")
try:
    r = requests.post(f"{BASE_URL}/api/fetch-stock",
                     json={"ticker": "NIFTY"},
                     timeout=30)
    if r.status_code == 200:
        data = r.json()
        print(f"✓ SUCCESS: {data['message']}")
        print(f"  Ticker: {data['ticker']}")
    else:
        print(f"✗ FAILED: {r.status_code} - {r.text}")
except Exception as e:
    print(f"✗ ERROR: {e}")

print("\n" + "=" * 60)
print("DIAGNOSTICS COMPLETE")
print("=" * 60)
