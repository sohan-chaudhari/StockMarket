from database import engine, SessionLocal
from sqlalchemy import text
import os

def run_migration():
    print("Running migration: 002_create_intraday_table.sql")
    
    file_path = os.path.join(os.path.dirname(__file__), "sql", "002_create_intraday_table.sql")
    
    with open(file_path, "r") as f:
        sql = f.read()
        
    try:
        with engine.connect() as connection:
            connection.execute(text(sql))
            connection.commit()
            print("Migration successful! Table 'intraday_candles_5min' created.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    run_migration()
