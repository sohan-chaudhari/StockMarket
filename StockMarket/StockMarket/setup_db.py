import urllib.parse
import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Add project paths to sys.path so we can import seed scripts
STOCK_DIR = r"C:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket"
NEWS_DIR = r"C:\Users\sohan\Desktop\StockMarket\StockMarket\News_Sentiment"

sys.path.append(STOCK_DIR)
sys.path.append(os.path.join(STOCK_DIR, "backend"))
sys.path.append(os.path.join(NEWS_DIR, "backend"))

def load_env_file(filepath):
    config = {}
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip().strip('"').strip("'")
    return config

def write_env_file(filepath, updates):
    if not os.path.exists(filepath):
        return
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    for line in lines:
        matched = False
        for k, v in updates.items():
            if line.strip().startswith(k + '='):
                new_lines.append(f"{k}={v}\n")
                matched = True
                break
        if not matched:
            new_lines.append(line)
            
    with open(filepath, 'w') as f:
        f.writelines(new_lines)

def main():
    print("========================================")
    print("     DATABASE SETUP & SEEDING TOOL      ")
    print("========================================\n")
    
    stock_env = load_env_file(os.path.join(STOCK_DIR, ".env"))
    news_env = load_env_file(os.path.join(NEWS_DIR, ".env"))
    
    db_password = stock_env.get("DB_PASSWORD", "medikart@3145")
    
    # Try connecting to postgres default db
    print("Testing connection to PostgreSQL...")
    conn = None
    try:
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password=db_password,
            database="postgres"
        )
        print("Connection successful using password from .env!")
    except psycopg2.OperationalError:
        print("Failed to connect using password from .env.")
        print("\nPlease enter your PostgreSQL 'postgres' user password:")
        db_password = input("Password: ").strip()
        try:
            conn = psycopg2.connect(
                host="localhost",
                port=5432,
                user="postgres",
                password=db_password,
                database="postgres"
            )
            print("Connection successful!")
        except Exception as e:
            print(f"\nError: Could not connect to PostgreSQL: {e}")
            print("\nPlease follow these steps to reset your password:")
            print("1. Open an Administrator Command Prompt.")
            print("2. Run: psql -U postgres")
            print("3. Enter SQL: ALTER USER postgres WITH PASSWORD 'medikart@3145';")
            print("4. Restart this setup script.")
            sys.exit(1)
            
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()
    
    # Create databases if they don't exist
    databases = ["stock_data", "news_sentiment"]
    for db in databases:
        cursor.execute(f"SELECT 1 FROM pg_database WHERE datname='{db}'")
        exists = cursor.fetchone()
        if not exists:
            print(f"Creating database '{db}'...")
            cursor.execute(f"CREATE DATABASE {db}")
        else:
            print(f"Database '{db}' already exists.")
            
    cursor.close()
    conn.close()
    
    # Sync password in env files
    print("\nUpdating configurations...")
    write_env_file(os.path.join(STOCK_DIR, ".env"), {"DB_PASSWORD": db_password})
    write_env_file(os.path.join(STOCK_DIR, "backend", ".env"), {"DB_PASSWORD": db_password})
    write_env_file(os.path.join(NEWS_DIR, ".env"), {
        "POSTGRES_PASSWORD": db_password,
        "DATABASE_URL": f"postgresql://postgres:{urllib.parse.quote_plus(db_password)}@localhost:5432/news_sentiment"
    })
    print("Configurations synced successfully.")
    
    # Seeding databases
    print("\nSeeding Stock Market database metadata...")
    try:
        from backend.seed_stocks import seed_stocks
        seed_stocks()
    except Exception as e:
        print(f"Error seeding stock metadata: {e}")
        
    print("\nSeeding News Sentiment database metadata...")
    try:
        from app.database import Base, engine as news_engine
        import app.models
        Base.metadata.create_all(bind=news_engine)
        
        from scripts.seed_tickers import seed_tickers
        seed_tickers()
    except Exception as e:
        print(f"Error seeding news sentiment tickers: {e}")
        
    print("\nDatabase setup completed successfully! ")
    print("Now you can run the services using:")
    print("  python start_all.py")

if __name__ == "__main__":
    main()
