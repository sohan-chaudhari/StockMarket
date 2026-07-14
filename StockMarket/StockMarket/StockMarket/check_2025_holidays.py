from backend.database import SessionLocal
from backend.models import Holiday
from datetime import date

db = SessionLocal()
holidays = db.query(Holiday).filter(Holiday.date >= date(2025, 1, 1), Holiday.date <= date(2025, 12, 31)).all()
print(f"Total 2025 Holidays: {len(holidays)}")
for h in holidays:
    print(h.date, h.description)

db.close()
