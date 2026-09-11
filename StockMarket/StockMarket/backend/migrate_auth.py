"""
Migration script to add new auth security columns and tables.
Run this once to update the database schema.
"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "YOUR_POSTGRES_PASSWORD")
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

print("Running auth security migrations...")

# Add columns to users table
migrations = [
    # Add failed_login_attempts column
    """
    DO $$ 
    BEGIN 
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                       WHERE table_name='users' AND column_name='failed_login_attempts') THEN
            ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0;
        END IF;
    END $$;
    """,
    
    # Add locked_until column
    """
    DO $$ 
    BEGIN 
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                       WHERE table_name='users' AND column_name='locked_until') THEN
            ALTER TABLE users ADD COLUMN locked_until TIMESTAMP NULL;
        END IF;
    END $$;
    """,
    
    # Create login_attempts table
    """
    CREATE TABLE IF NOT EXISTS login_attempts (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255),
        ip_address VARCHAR(45),
        user_agent VARCHAR(500),
        success BOOLEAN DEFAULT FALSE,
        failure_reason VARCHAR(100),
        attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_login_email ON login_attempts(email);
    CREATE INDEX IF NOT EXISTS idx_login_time ON login_attempts(attempted_at);
    """,
    
    # Create verification_tokens table
    """
    CREATE TABLE IF NOT EXISTS verification_tokens (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        token_hash VARCHAR(255) NOT NULL,
        otp_code VARCHAR(6),
        token_type VARCHAR(50) NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_verification_user ON verification_tokens(user_id);
    CREATE INDEX IF NOT EXISTS idx_verification_type ON verification_tokens(token_type);
    """,
    
    # Create token_blacklist table
    """
    CREATE TABLE IF NOT EXISTS token_blacklist (
        id SERIAL PRIMARY KEY,
        token_jti VARCHAR(255) UNIQUE NOT NULL,
        blacklisted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_blacklist_jti ON token_blacklist(token_jti);
    """
]

for sql in migrations:
    try:
        cur.execute(sql)
        print("✓ Migration executed successfully")
    except Exception as e:
        print(f"! Migration warning: {e}")

cur.close()
conn.close()

print("\n✅ All migrations complete!")
