from sqlalchemy import create_engine, MetaData, Table, select
from backend.database import SQLALCHEMY_DATABASE_URL
from datetime import date

engine = create_engine(SQLALCHEMY_DATABASE_URL)
metadata = MetaData()
candle_table = Table('current_day_candle', metadata, autoload_with=engine)

with engine.connect() as conn:
    stmt = select(candle_table).where(candle_table.c.ticker == 'RELIANCE.NS').where(candle_table.c.trading_date == date.today())
    result = conn.execute(stmt).fetchone()
    if result:
        print(f"Ticker: {result.ticker}")
        print(f"Date: {result.trading_date}")
        print(f"Volume: {result.volume}")
        print(f"Last Updated: {result.last_updated}")
    else:
        print("No candle found for RELIANCE.NS today")
