import requests
from backend.database import SessionLocal
from backend.models import StockData
from sqlalchemy import delete

# Configuration
BASE_URL = "http://localhost:8000/api"
TICKER_TO_TEST = "CIPLA.NS"

def verify_pipeline():
    db = SessionLocal()
    try:
        print(f"1. Cleaning DB for {TICKER_TO_TEST}...")
        db.query(StockData).filter(StockData.ticker == TICKER_TO_TEST).delete()
        db.commit()
        
        # Verify Empty
        count = db.query(StockData).filter(StockData.ticker == TICKER_TO_TEST).count()
        print(f"   DB Count after clean: {count} (Expected: 0)")
        if count != 0:
            raise Exception("Failed to clean DB")

        print(f"2. Triggering /fetch-stock (Simulating Search)...")
        resp = requests.post(f"{BASE_URL}/fetch-stock", json={"ticker": TICKER_TO_TEST})
        if resp.status_code != 200:
            raise Exception(f"API Failed: {resp.text}")
        
        data = resp.json()
        print(f"   API Response Status: {data.get('status')}")
        print(f"   API Returned Records: {data.get('count')}")
        
        if data.get('count') == 0:
             raise Exception("API returned 0 records. YFinance fetch might have failed.")

        print(f"3. Verifying Persistence in DB...")
        final_count = db.query(StockData).filter(StockData.ticker == TICKER_TO_TEST).count()
        print(f"   DB Count after fetch: {final_count}")
        
        if final_count > 0:
            print("SUCCESS: Data was fetched from YFinance and stored in DB.")
        else:
            print("FAILURE: Data was returned but NOT stored in DB.")

    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    verify_pipeline()
