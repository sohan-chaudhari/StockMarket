"""Test the fixed fetch logic"""
import requests

print("Testing fixed /api/fetch-stock...")
r = requests.post("http://localhost:8000/api/fetch-stock",
                 json={"ticker": "INFY"},
                 timeout=60)

if r.status_code == 200:
    data = r.json()
    print(f"✓ Fetch succeeded")
    print(f"  Message: {data['message']}")
    print(f"  Count: {data['count']}")
    
    # Verify DB
    from backend.database import SessionLocal
    from backend import models
    db = SessionLocal()
    count = db.query(models.StockData).filter(models.StockData.ticker == "INFY.NS").count()
    print(f"  DB records: {count}")
    db.close()
    
    if count > 2000:
        print("✓ SUCCESS: Full history preserved!")
    else:
        print(f"✗ FAILED: Only {count} records in DB")
else:
    print(f"✗ Failed: {r.status_code}")
    print(r.text)
