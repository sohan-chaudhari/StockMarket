"""
Add SENSEX (NSE) to stock_metadata so both BSE and NSE appear in search
"""
import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models

db = SessionLocal()

# Indices to add/update
indices = [
    {
        'ticker': 'SENSEX', 
        'name': 'SENSEX', 
        'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg', 
        'exchange': 'NSE'
    }
]

print("Adding SENSEX (NSE) to stock_metadata...")
print("=" * 60)

for idx in indices:
    # Check if this specific combination already exists
    # We allow multiple entries for the same ticker if exchanges are different
    existing = db.query(models.StockMetadata).filter(
        models.StockMetadata.ticker == idx['ticker'],
        models.StockMetadata.exchange == idx['exchange']
    ).first()
    
    if existing:
        print(f"✓ {idx['ticker']} ({idx['exchange']}) - Already exists")
        continue
    
    # Add new
    new_stock = models.StockMetadata(
        ticker=idx['ticker'],
        name=idx['name'],
        logo=idx['logo'],
        exchange=idx['exchange'],
        base_price=0
    )
    
    db.add(new_stock)
    print(f"✓ {idx['ticker']} ({idx['exchange']}) - Added")

db.commit()
db.close()

print("=" * 60)
print("SENSEX (NSE) added successfully!")
