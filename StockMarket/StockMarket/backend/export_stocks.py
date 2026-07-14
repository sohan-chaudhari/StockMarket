from backend.database import SessionLocal
from backend import models
import csv
import os

def export():
    db = SessionLocal()
    stocks = db.query(models.StockMetadata).all()
    
    file_path = "e:\\StockMarket\\stocks_list.csv"
    
    print(f"Exporting {len(stocks)} stocks to {file_path}...")
    
    with open(file_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # Header
        writer.writerow(["id", "ticker", "name", "exchange", "logo", "base_price"])
        
        for s in stocks:
            writer.writerow([
                s.id, 
                s.ticker, 
                s.name, 
                s.exchange, 
                s.logo, 
                s.base_price
            ])
            
    print("Export Complete.")
    db.close()

if __name__ == "__main__":
    export()
