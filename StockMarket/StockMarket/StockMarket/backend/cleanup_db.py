from database import engine
from sqlalchemy import text

def cleanup_tables():
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        try:
            print("Cleaning up blocking tables...")
            
            # Delete data from transactions
            connection.execute(text("TRUNCATE TABLE transactions CASCADE"))
            print(" - Truncated 'transactions' table.")
            
            # Delete data from watchlist
            connection.execute(text("TRUNCATE TABLE watchlist CASCADE"))
            print(" - Truncated 'watchlist' table.")
            
            print("\nSUCCESS: All blocking data has been removed.")
            
        except Exception as e:
            print(f"Error during cleanup: {e}")
            # Fallback to DELETE if TRUNCATE fails (e.g. permissions)
            try:
                connection.execute(text("DELETE FROM transactions"))
                connection.execute(text("DELETE FROM watchlist"))
                print(" - Deleted all rows (Fallback).")
            except Exception as ex:
                print(f"Fallback failed: {ex}")

if __name__ == "__main__":
    cleanup_tables()
