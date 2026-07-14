"""
Fix database schema by dropping incorrect tables and recreating them.
"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "medikart@3145")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "stock_data")

conn = psycopg2.connect(
    host=DB_HOST,
    database=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
    port=DB_PORT
)
conn.autocommit = True
cur = conn.cursor()

print("Fixing schema...")

# Drop tables if they exist
tables = ['verification_tokens', 'login_attempts', 'token_blacklist']
for table in tables:
    try:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
        print(f"Dropped table {table}")
    except Exception as e:
        print(f"Error dropping {table}: {e}")

# Re-create tables with correct types
commands = [
    """
    CREATE TABLE login_attempts (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255),
        ip_address VARCHAR(45),
        user_agent VARCHAR(500),
        success BOOLEAN DEFAULT FALSE,
        failure_reason VARCHAR(100),
        attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_login_email ON login_attempts(email);
    """,
    
    """
    CREATE TABLE verification_tokens (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        token_hash VARCHAR(255) NOT NULL,
        otp_code VARCHAR(6),
        token_type VARCHAR(50) NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_verification_user ON verification_tokens(user_id);
    """,
    
    """
    CREATE TABLE token_blacklist (
        id SERIAL PRIMARY KEY,
        token_jti VARCHAR(255) UNIQUE NOT NULL,
        blacklisted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_blacklist_jti ON token_blacklist(token_jti);
    """
]

for sql in commands:
    try:
        cur.execute(sql)
        print("✓ Created table successfully")
    except Exception as e:
        print(f"! Error creating table: {e}")

cur.close()
conn.close()

print("\n✅ Schema fix complete!")
