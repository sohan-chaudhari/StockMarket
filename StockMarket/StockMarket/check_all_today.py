from sqlalchemy import create_engine, MetaData, Table, select
from backend.database import SQLALCHEMY_DATABASE_URL
from datetime import date

engine = create_engine(SQLALCHEMY_DATABASE_URL)
metadata = MetaData()
candle_table = Table('current_day_candle', metadata, autoload_with=engine)

with engine.connect() as conn:
    stmt = select(candle_table).where(candle_table.c.trading_date == date.today())
    results = conn.execute(stmt).fetchall()
    print(f"Total entries for today: {len(results)}")
    for r in results[:10]:
        print(f"Ticker: {r.ticker}, Volume: {r.volume}")
