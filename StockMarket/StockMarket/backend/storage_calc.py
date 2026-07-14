import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DB_URL = "postgresql://postgres:medikart%403145@localhost/stock_data"

engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    # 1. Ticker count
    ticker_count = session.execute(text("SELECT count(*) FROM stock_metadata")).scalar()
    
    # 2. Table sizes
    tables = session.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).fetchall()
    tables = [t[0] for t in tables]
    print(f"Total Tickers: {ticker_count}")
    print("--- Current Tables in DB ---")
    print(tables)
    print("--- Current Table Sizes ---")
    
    for table in tables:
        try:
            size = session.execute(text(f"SELECT pg_total_relation_size('{table}')")).scalar()
            count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            
            if table == 'candles':
                tf_counts = session.execute(text("SELECT timeframe, count(*), pg_column_size(timeframe) * count(*) FROM candles GROUP BY timeframe")).fetchall()
                print(f"Table {table}: Size = {size / (1024*1024):.2f} MB, Rows = {count}")
                for tf, tf_count, _ in tf_counts:
                    print(f"  - {tf}: {tf_count} rows")
            else:
                print(f"Table {table}: Size = {size / (1024*1024):.2f} MB, Rows = {count}")
        except Exception as e:
            session.rollback()

    # 3. Average row size for calculations
    try:
        avg_row_size = session.execute(text("SELECT avg(pg_column_size(candles.*)) FROM candles LIMIT 1000")).scalar()
        print(f"Average row size in candles table: {avg_row_size} bytes")
    except Exception as e:
        print(f"Error getting row size: {e}")

except Exception as e:
    print(f"Error: {e}")
finally:
    session.close()
