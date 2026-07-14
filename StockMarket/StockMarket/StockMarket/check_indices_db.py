import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models

db = SessionLocal()

# Check if indices exist in database
indices = db.query(models.StockMetadata).filter(
    models.StockMetadata.ticker.in_(['NIFTY', 'BANKNIFTY', 'SENSEX'])
).all()

print(f"Found {len(indices)} indices in database:")
for idx in indices:
    print(f"  - {idx.ticker}: {idx.name}")

db.close()
