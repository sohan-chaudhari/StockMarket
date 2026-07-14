import urllib.parse
import pandas as pd
from sqlalchemy import create_engine
import getpass

def import_csv_to_postgres():
    csv_file_path = r'e:\StockMarket\historical_data\INFY.NS_daily.csv'
    
    print("Please enter your PostgreSQL connection details:")
    host = input("Host (default: localhost): ") or "localhost"
    port = input("Port (default: 5432): ") or "5432"
    dbname = input("Database Name: ")
    user = input("Username (default: postgres): ") or "postgres"
    password = getpass.getpass("Password: ")

    connection_string = f'postgresql://{user}:{urllib.parse.quote_plus(password)}@{host}:{port}/{dbname}'
    
    try:
        print("Connecting to database...")
        engine = create_engine(connection_string)
        
        print(f"Reading CSV file: {csv_file_path}")
        df = pd.read_csv(csv_file_path)
        
        # Ensure column names are standard SQL friendly (lowercase, no spaces)
        df.columns = [c.lower().replace(' ', '_') for c in df.columns]
        
        table_name = 'infy_daily'
        print(f"Importing data into table '{table_name}'...")
        df.to_sql(table_name, engine, if_exists='replace', index=False)
        
        print("Import successful!")
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    import_csv_to_postgres()
