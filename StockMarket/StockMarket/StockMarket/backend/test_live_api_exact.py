import asyncio
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
try:
    mm_res = client.get("/api/market-movers")
    if mm_res.status_code == 200:
        data = mm_res.json()
        allTickers = []
        for cat in ['gainers', 'losers', 'most_active']:
            for item in data.get(cat, []):
                if item.get('ticker') and item['ticker'] not in allTickers:
                    allTickers.append(item['ticker'])
        
        print("allTickers:", allTickers)
        response = client.post("/api/live-prices", json={"tickers": allTickers})
        print(response.status_code)
        if response.status_code == 500:
            print(response.text)
except Exception as e:
    import traceback
    traceback.print_exc()
