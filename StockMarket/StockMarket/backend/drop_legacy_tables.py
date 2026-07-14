from database import engine
from sqlalchemy import text

def drop_tables():
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        print("Dropping legacy tables...")
        try:
            connection.execute(text("DROP TABLE IF EXISTS transactions CASCADE"))
            print(" - Dropped 'transactions'")
        except Exception as e:
            print(f" - Error transactions: {e}")
            
        try:
            connection.execute(text("DROP TABLE IF EXISTS watchlist CASCADE"))
            print(" - Dropped 'watchlist'")
        except Exception as e:
            print(f" - Error watchlist: {e}")

        try:
            connection.execute(text("DROP TABLE IF EXISTS holdings CASCADE"))
            print(" - Dropped 'holdings'")
        except Exception as e:
            print(f" - Error holdings: {e}")
            
    print("\nCleanup complete.")

if __name__ == "__main__":
    drop_tables()
