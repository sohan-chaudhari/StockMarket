"""
Add indices to stock_metadata table so they appear in search
"""
import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models

db = SessionLocal()

# Indices to add
indices = [
    {'ticker': 'NIFTY', 'name': 'NIFTY 50', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
    {'ticker': 'BANKNIFTY', 'name': 'BANK NIFTY', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
    {'ticker': 'SENSEX', 'name': 'BSE SENSEX', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'}
]

print("Adding indices to stock_metadata...")
print("=" * 60)

for idx in indices:
    # Check if already exists
    existing = db.query(models.StockMetadata).filter(
        models.StockMetadata.ticker == idx['ticker']
    ).first()
    
    if existing:
        print(f"✓ {idx['ticker']:12} - Already exists")
        continue
    
    # Add new
    new_stock = models.StockMetadata(
        ticker=idx['ticker'],
        name=idx['name'],
        logo=idx['logo'],
        base_price=0  # Will be updated from live data
    )
    
    db.add(new_stock)
    print(f"✓ {idx['ticker']:12} - Added ({idx['name']})")

db.commit()
db.close()

print("=" * 60)
print("Indices added to stock_metadata successfully!")
print("They will now appear in search results.")
