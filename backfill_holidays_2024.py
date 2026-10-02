from backend.database import SessionLocal
from backend.models import Holiday
from datetime import date

db = SessionLocal()

holidays_2024 = [
    (date(2024, 1, 22), "Special Holiday"),
    (date(2024, 1, 26), "Republic Day"),
    (date(2024, 3, 8), "Mahashivratri"),
    (date(2024, 3, 25), "Holi"),
    (date(2024, 3, 29), "Good Friday"),
    (date(2024, 4, 11), "Id-Ul-Fitr (Ramzan Id)"),
    (date(2024, 4, 17), "Shri Ram Navami"),
    (date(2024, 5, 1), "Maharashtra Day"),
    (date(2024, 6, 17), "Bakri Id"),
    (date(2024, 7, 17), "Moharram"),
    (date(2024, 8, 15), "Independence Day"),
    (date(2024, 10, 2), "Mahatma Gandhi Jayanti"),
    (date(2024, 11, 1), "Diwali Laxmi Pujan"),
    (date(2024, 11, 15), "Gurunanak Jayanti"),
    (date(2024, 12, 25), "Christmas")
]

count = 0
for d, desc in holidays_2024:
    exists = db.query(Holiday).filter(Holiday.date == d).first()
    if not exists:
        h = Holiday(date=d, description=desc)
        db.add(h)
        count += 1

db.commit()
print(f"Added {count} holidays for 2024.")
db.close()
