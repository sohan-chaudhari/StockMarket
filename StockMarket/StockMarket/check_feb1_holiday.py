"""
Quick script to check if Feb 1st 2026 is marked as a holiday
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.database import SessionLocal
from backend.models import Holiday
from datetime import date

db = SessionLocal()

# Check Feb 1st
feb_1 = date(2026, 2, 1)
holiday = db.query(Holiday).filter(Holiday.date == feb_1).first()

if holiday:
    print(f"❌ Feb 1st IS marked as holiday: {holiday.name}")
    print(f"   Deleting this incorrect holiday entry...")
    db.delete(holiday)
    db.commit()
    print(f"   ✓ Deleted!")
else:
    print(f"✓ Feb 1st is NOT marked as a holiday (correct)")

# Show nearby holidays
print("\nHolidays around Feb 1st:")
holidays = db.query(Holiday).filter(
    Holiday.date.between(date(2026, 1, 25), date(2026, 2, 5))
).order_by(Holiday.date).all()

for h in holidays:
    print(f"  {h.date} - {h.name}")

db.close()
