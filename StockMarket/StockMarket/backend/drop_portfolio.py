from database import engine
from sqlalchemy import text

def drop_portfolio():
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        try:
            print("Dropping 'portfolio' table...")
            connection.execute(text("DROP TABLE IF EXISTS portfolio CASCADE"))
            print(" - Dropped 'portfolio' (if it existed).")
            
            # Just in case they meant 'holdings' which acts as portfolio
            # connection.execute(text("DROP TABLE IF EXISTS holdings CASCADE"))
            # print(" - Dropped 'holdings' (if it existed).") 
            
            print("\nSUCCESS.")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    drop_portfolio()
