from backend.database import SessionLocal
from backend.models import Holiday
from datetime import date

db = SessionLocal()
holidays = db.query(Holiday).all()
print("Total Holidays:", len(holidays))
for h in holidays:
    if h.date.year >= 2024:
        print(h.date, h.description)

db.close()
