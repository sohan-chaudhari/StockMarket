import requests
import json
import os
import psycopg2
from urllib.parse import quote_plus

print("1. Checking connection to local backend api/top-9-history...")
try:
    resp = requests.get("http://127.0.0.1:8003/api/top-9-history", timeout=5)
    print(f"Status code: {resp.status_code}")
    print("Response data:")
    print(json.dumps(resp.json(), indent=2)[:1000])
except Exception as e:
    print(f"Failed to fetch from backend API: {e}")

print("\n2. Checking connection to database and listing table rows...")
DB_USER = "postgres"
DB_PASSWORD = "medikart@3145"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "stock_data"

try:
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )
    cur = conn.cursor()
    
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"Tables in database: {tables}")
    
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f" - Table '{table}': {count} rows")
        if count > 0:
            cur.execute(f"SELECT * FROM {table} LIMIT 1")
            row = cur.fetchone()
            print(f"   Sample row: {row}")
            
    cur.close()
    conn.close()
except Exception as e:
    print(f"Failed to connect/query DB: {e}")
