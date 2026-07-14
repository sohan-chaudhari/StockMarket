from database import engine
from sqlalchemy import text

def force_fix():
    # Use autocommit to ensure statements run immediately
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        print("Force fixing Transactions...")
        try:
            connection.execute(text("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_user_id_fkey"))
            print(" - Dropped.")
        except Exception as e:
            print(f" - Drop Error: {e}")
            
        try:
            connection.execute(text("ALTER TABLE transactions ADD CONSTRAINT transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"))
            print(" - Added CASCADE.")
        except Exception as e:
             print(f" - Add Error: {e}")

        print("\nForce fixing Watchlist...")
        try:
            connection.execute(text("ALTER TABLE watchlist DROP CONSTRAINT IF EXISTS watchlist_user_id_fkey"))
            print(" - Dropped.")
        except Exception as e:
            print(f" - Drop Error: {e}")
            
        try:
            connection.execute(text("ALTER TABLE watchlist ADD CONSTRAINT watchlist_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"))
            print(" - Added CASCADE.")
        except Exception as e:
            print(f" - Add Error: {e}")
            
        print("\nVerifying...")
        result = connection.execute(text("SELECT constraint_name, delete_rule FROM information_schema.referential_constraints WHERE constraint_name IN ('transactions_user_id_fkey', 'watchlist_user_id_fkey')"))
        for row in result:
             print(f"{row.constraint_name} -> {row.delete_rule}")

if __name__ == "__main__":
    force_fix()
