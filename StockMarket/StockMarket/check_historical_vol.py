from sqlalchemy import create_engine, MetaData, Table, select
from backend.database import SQLALCHEMY_DATABASE_URL

engine = create_engine(SQLALCHEMY_DATABASE_URL)
metadata = MetaData()
stock_data = Table('stock_data', metadata, autoload_with=engine)

with engine.connect() as conn:
    stmt = select(stock_data).where(stock_data.c.ticker == 'RELIANCE.NS').order_by(stock_data.c.date.desc()).limit(10)
    results = conn.execute(stmt).fetchall()
    for r in results:
        print(f"Date: {r.date}, Volume: {r.volume}, Close: {r.close}")
