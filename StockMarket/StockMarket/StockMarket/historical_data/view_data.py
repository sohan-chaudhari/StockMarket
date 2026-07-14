import urllib.parse
import pandas as pd
from sqlalchemy import create_engine
import getpass

def view_data():
    print("Please enter your PostgreSQL connection details to view data:")
    host = input("Host (default: localhost): ") or "localhost"
    port = input("Port (default: 5432): ") or "5432"
    dbname = input("Database Name: ")
    user = input("Username (default: postgres): ") or "postgres"
    password = getpass.getpass("Password: ")

    connection_string = f'postgresql://{user}:{urllib.parse.quote_plus(password)}@{host}:{port}/{dbname}'
    
    try:
        engine = create_engine(connection_string)
        
        query = "SELECT * FROM infy_daily LIMIT 10;"
        print(f"\nExecuting query: {query}\n")
        
        df = pd.read_sql(query, engine)
        
        if not df.empty:
            print(df.to_string())
            print(f"\nSuccessfully retrieved {len(df)} rows.")
        else:
            print("Table 'infy_daily' is empty or does not exist.")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    view_data()
