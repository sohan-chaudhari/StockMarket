from database import engine
from sqlalchemy import text

def drop_users_auth():
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        print("Dropping users_auth table...")
        try:
            connection.execute(text("DROP TABLE IF EXISTS users_auth CASCADE"))
            print(" - Dropped 'users_auth'")
        except Exception as e:
            print(f" - Error dropping users_auth: {e}")
            
    print("\nCleanup complete.")

if __name__ == "__main__":
    drop_users_auth()
