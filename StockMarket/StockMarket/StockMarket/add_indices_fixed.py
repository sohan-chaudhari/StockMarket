"""
Add indices to stock_metadata table (with proper commit)
"""
import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models

db = SessionLocal()

# Indices to add
indices = [
    {'ticker': 'NIFTY', 'name': 'NIFTY 50', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg', 'exchange': 'NSE'},
    {'ticker': 'BANKNIFTY', 'name': 'BANK NIFTY', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg', 'exchange': 'NSE'},
    {'ticker': 'SENSEX', 'name': 'BSE SENSEX', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg', 'exchange': 'BSE'}
]

print("Adding indices to stock_metadata...")
print("=" * 60)

added_count = 0

try:
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
            exchange=idx['exchange'],
            base_price=0  # Will be updated from live data
        )
        
        db.add(new_stock)
        added_count += 1
        print(f"✓ {idx['ticker']:12} - Added ({idx['name']})")
    
    # Commit all changes
    db.commit()
    print("\n✅ Changes committed to database!")
    
    # Verify
    print("\nVerifying...")
    for idx in indices:
        check = db.query(models.StockMetadata).filter(
            models.StockMetadata.ticker == idx['ticker']
        ).first()
        if check:
            print(f"  ✓ {check.ticker} confirmed in database")
        else:
            print(f"  ✗ {idx['ticker']} NOT found!")
    
except Exception as e:
    print(f"\n✗ Error: {e}")
    db.rollback()
finally:
    db.close()

print("=" * 60)
print(f"Added {added_count} new indices")
print("They will now appear in search results after page refresh!")
