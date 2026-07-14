import sys
sys.path.append('backend')

from backend.database import SessionLocal
from backend import models
from datetime import date

db = SessionLocal()

# Check if Feb 2 is in holidays
feb2 = date(2026, 2, 2)
holiday = db.query(models.Holiday).filter(models.Holiday.date == feb2).first()

if holiday:
    print(f"Feb 2, 2026 IS A HOLIDAY: {holiday.description}")
    print("This explains why there's no data!")
else:
    print("Feb 2, 2026 is NOT a holiday in the database")
    print("\nThis means data sync hasn't run yet for Feb 2.")
    print("The market was open on Feb 2 (Monday), but historical data hasn't been fetched.")

# Check Feb 1 (Sunday - should be weekend)
feb1 = date(2026, 2, 1)
print(f"\nFeb 1, 2026 is {feb1.strftime('%A')} (weekday={feb1.weekday()})")

db.close()
