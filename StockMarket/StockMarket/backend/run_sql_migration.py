import urllib.parse
"""
Run SQL Migration Script
Utilities to run SQL files using SQLAlchemy
"""
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# Database connection
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "medikart@3145")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "stock_data")

DATABASE_URL = f"postgresql://{DB_USER}:{urllib.parse.quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def run_migration_file(file_path):
    print(f"Running migration: {file_path}")
    engine = create_engine(DATABASE_URL)
    
    with open(file_path, 'r') as f:
        sql_content = f.read()
        
    with engine.connect() as connection:
        # Split by ; to run multiple statements if needed, or run as block
        # Simple split might break if ; is inside strings, but for this file it's likely fine
        # Better to try executing the whole block if it's DDL
        try:
            connection.execute(text(sql_content))
            connection.commit()
            print("Migration successful.")
        except Exception as e:
            print(f"Error running migration: {e}")
            # Try splitting if simple execution failed (e.g. mixed commands)
            # But the file 001_create_tickers_table.sql has simple DDL
            
if __name__ == "__main__":
    import pathlib
    # Path to 001_create_tickers_table.sql
    # C:\Users\rahul\Desktop\StockMarket\StockMarket\backend\migrations\timescaledb\001_create_tickers_table.sql
    migration_path = os.path.join(
        os.path.dirname(__file__), 
        "migrations", "timescaledb", "001_create_tickers_table.sql"
    )
    
    if os.path.exists(migration_path):
        run_migration_file(migration_path)
    else:
        print(f"File not found: {migration_path}")
