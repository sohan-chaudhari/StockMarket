"""
Script to add a holiday to the database.
"""
from datetime import date
from backend.database import SessionLocal, engine
from backend import models

# Ensure table exists
models.Base.metadata.create_all(bind=engine)

def add_holiday(holiday_date: date, description: str):
    db = SessionLocal()
    try:
        # Check if already exists
        existing = db.query(models.Holiday).filter(models.Holiday.date == holiday_date).first()
        if existing:
            print(f"Holiday already exists: {holiday_date} - {existing.description}")
            return
        
        # Add new holiday
        holiday = models.Holiday(date=holiday_date, description=description)
        db.add(holiday)
        db.commit()
        print(f"Added holiday: {holiday_date} - {description}")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

def list_holidays():
    db = SessionLocal()
    try:
        holidays = db.query(models.Holiday).order_by(models.Holiday.date.desc()).limit(20).all()
        print(f"\n=== Recent Holidays ({len(holidays)}) ===")
        for h in holidays:
            print(f"  {h.date} - {h.description}")
    finally:
        db.close()

if __name__ == "__main__":
    # Add January 15, 2026 as a holiday
    add_holiday(date(2026, 1, 15), "Pongal / Makar Sankranti")
    
    # Show all holidays
    list_holidays()
