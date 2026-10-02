from backend.database import SessionLocal
from backend.models import Holiday
from datetime import date

db = SessionLocal()

holidays_2025 = [
     (date(2025, 2, 26), "Mahashivratri"),
     (date(2025, 3, 14), "Holi"),
     (date(2025, 3, 31), "Id-Ul-Fitr (Ramzan Id)"),
     (date(2025, 4, 10), "Shri Ram Navami"), # Tentative
     (date(2025, 4, 14), "Dr. Baba Saheb Ambedkar Jayanti"),
     (date(2025, 4, 18), "Good Friday"),
     (date(2025, 5, 1), "Maharashtra Day"),
     (date(2025, 6, 7), "Bakri Id"), # Tentative, Sat
     (date(2025, 8, 15), "Independence Day"),
     (date(2025, 8, 27), "Ganesh Chaturthi"),
     (date(2025, 10, 2), "Mahatma Gandhi Jayanti"),
     (date(2025, 10, 21), "Diwali-Laxmi Pujan"),
     (date(2025, 11, 5), "Gurunanak Jayanti"),
     (date(2025, 12, 25), "Christmas")
]

count = 0
for d, desc in holidays_2025:
    exists = db.query(Holiday).filter(Holiday.date == d).first()
    if not exists:
        h = Holiday(date=d, description=desc)
        db.add(h)
        count += 1

db.commit()
print(f"Added {count} holidays for 2025.")
db.close()
