from sqlalchemy import create_engine, MetaData, Table, select
from backend.database import SQLALCHEMY_DATABASE_URL
from datetime import date

engine = create_engine(SQLALCHEMY_DATABASE_URL)
metadata = MetaData()
holiday_table = Table('holidays', metadata, autoload_with=engine)

with engine.connect() as conn:
    stmt = select(holiday_table)
    results = conn.execute(stmt).fetchall()
    print(f"Total Holidays: {len(results)}")
    for r in results:
        if r.date.year == 2026:
            print(f"Holiday: {r.date} - {r.description}")

# Also check TODAY'S date in python
print(f"Python Today: {date.today()}")
