from database import engine
from sqlalchemy import text

def migrate():
    with engine.connect() as conn:
        print("Adding stock_name to positions table...")
        try:
            conn.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS stock_name VARCHAR(255)"))
            conn.commit()
            print("Success.")
        except Exception as e:
            print(f"Error: {e}")

        print("Adding stock_name to transactions table...")
        try:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS stock_name VARCHAR(255)"))
            conn.commit()
            print("Success.")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    migrate()
