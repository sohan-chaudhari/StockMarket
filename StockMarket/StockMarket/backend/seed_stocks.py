from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database import SessionLocal, engine
from backend import models
import json
import os

def seed_stocks():
    # DROP table if exists to handle Schema Change (PK change)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS stock_metadata"))
        conn.commit()
        
    models.Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    
    json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'stocks_temp.json')
    if not os.path.exists(json_path):
        json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'stocks.json')
    print(f"Reading from {json_path}...")
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            stocks_list = json.load(f)
        print(f"Parsed {len(stocks_list)} stocks.")
        
        # Batch Insert
        batch = []
        for item in stocks_list:
            stock = models.StockMetadata(
                ticker=item.get('ticker'),
                name=item.get('name'),
                exchange=item.get('exchange'),
                logo=item.get('logo'),
                base_price=item.get('basePrice')
            )
            batch.append(stock)
            
            if len(batch) >= 1000:
                db.add_all(batch)
                db.commit()
                batch = []
        
        if batch:
            db.add_all(batch)
            db.commit()
            
        print(f"Successfully seeded {len(stocks_list)} stocks.")
        
    except Exception as e:
        print(f"Error seeding stocks: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_stocks()
