from backend.database import engine
from sqlalchemy import text

def add_columns():
    with engine.connect() as conn:
        # Commit mode is required for DDL in some configurations, mainly for psycopg2 if autocommit is not set.
        # But verify if transaction is needed.
        # `conn.execute` usually works inside a transaction if not autocommit.
        # We need to commit.
        trans = conn.begin()
        try:
            print("Adding take_profit column...")
            conn.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS take_profit FLOAT"))
            print("Adding stop_loss column...")
            conn.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS stop_loss FLOAT"))
            trans.commit()
            print("Schema updated successfully!")
        except Exception as e:
            trans.rollback()
            print(f"Error: {e}")

if __name__ == "__main__":
    add_columns()
