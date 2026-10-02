"""
Database Migration Script: SQLite (stocks.db) → PostgreSQL (stock_data)

This script migrates all schema and data from the old SQLite database 
to the new PostgreSQL database with minimal downtime.

Usage:
    python migrate_sqlite_to_postgres.py
"""

import sqlite3
import psycopg2
from psycopg2 import sql
from datetime import datetime
import sys

# PostgreSQL connection details (from backend/.env)
PG_CONFIG = {
    'dbname': 'stock_data',
    'user': 'postgres',
    'password': 'YOUR_POSTGRES_PASSWORD',
    'host': 'localhost',
    'port': '5432'
}

# SQLite database path
SQLITE_DB = 'stocks.db'

# Table mappings (SQLite → PostgreSQL)
# These should match the models in models.py
TABLES = [
    'stock_data',
    'intraday_ticks',
    'current_day_candle',
    'holidays',
    'stock_metadata',
    'users',
    'login_attempts',
    'verification_tokens',
    'token_blacklist',
    'positions',
    'orders',
    'transactions'
]


def connect_sqlite():
    """Connect to SQLite database"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        conn.row_factory = sqlite3.Row  # Return dictionaries
        print(f"✅ Connected to SQLite: {SQLITE_DB}")
        return conn
    except Exception as e:
        print(f"❌ Error connecting to SQLite: {e}")
        sys.exit(1)


def connect_postgres():
    """Connect to PostgreSQL database"""
    try:
        conn = psycopg2.connect(**PG_CONFIG)
        print(f"✅ Connected to PostgreSQL: {PG_CONFIG['dbname']}")
        return conn
    except Exception as e:
        print(f"❌ Error connecting to PostgreSQL: {e}")
        print("\nTroubleshooting:")
        print("1. Verify PostgreSQL is running: Get-Service postgresql-x64-18")
        print("2. Check password in backend\\.env matches PostgreSQL")
        print("3. Ensure database 'stock_data' exists")
        sys.exit(1)


def get_table_info(sqlite_conn, table_name):
    """Get table structure from SQLite"""
    cursor = sqlite_conn.cursor()
    cursor.execute(f"PRAGMA table_info([{table_name}])")
    return cursor.fetchall()


def get_row_count(sqlite_conn, table_name):
    """Get number of rows in SQLite table"""
    cursor = sqlite_conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM [{table_name}]")
    return cursor.fetchone()[0]


def create_schema(pg_conn):
    """
    Create tables in PostgreSQL using SQLAlchemy models
    This should be handled by models.Base.metadata.create_all()
    """
    print("\n📋 Schema Creation:")
    print("   Schema will be created automatically by SQLAlchemy when server starts")
    print("   Ensure line 29 in backend/main.py is uncommented:")
    print("   → models.Base.metadata.create_all(bind=database.engine)")
    return True


def migrate_table_data(sqlite_conn, pg_conn, table_name):
    """Migrate data from SQLite table to PostgreSQL table"""
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        # Get all rows from SQLite
        sqlite_cursor.execute(f"SELECT * FROM [{table_name}]")
        rows = sqlite_cursor.fetchall()
        
        if len(rows) == 0:
            print(f"   ⊘ {table_name}: 0 rows (empty table, skipped)")
            return 0
        
        # Get column names
        columns = [description[0] for description in sqlite_cursor.description]
        
        # Prepare PostgreSQL INSERT statement
        placeholders = ', '.join(['%s'] * len(columns))
        column_names = ', '.join([f'"{col}"' for col in columns])
        insert_query = f'INSERT INTO {table_name} ({column_names}) VALUES ({placeholders})'
        
        # Insert all rows
        inserted = 0
        for row in rows:
            try:
                pg_cursor.execute(insert_query, tuple(row))
                inserted += 1
            except Exception as e:
                print(f"      ⚠️ Error inserting row: {e}")
                continue
        
        pg_conn.commit()
        print(f"   ✅ {table_name}: {inserted}/{len(rows)} rows migrated")
        return inserted
        
    except Exception as e:
        print(f"   ❌ {table_name}: Migration failed - {e}")
        pg_conn.rollback()
        return 0


def verify_migration(sqlite_conn, pg_conn):
    """Verify data migration by comparing row counts"""
    print("\n🔍 Verification:")
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    all_match = True
    for table in TABLES:
        try:
            # SQLite count
            sqlite_cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
            sqlite_count = sqlite_cursor.fetchone()[0]
            
            # PostgreSQL count
            pg_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = pg_cursor.fetchone()[0]
            
            match = "✅" if sqlite_count == pg_count else "❌"
            print(f"   {match} {table}: SQLite={sqlite_count}, PostgreSQL={pg_count}")
            
            if sqlite_count != pg_count:
                all_match = False
                
        except Exception as e:
            print(f"   ⚠️ {table}: Could not verify - {e}")
            all_match = False
    
    return all_match


def main():
    """Main migration process"""
    print("=" * 60)
    print("DATABASE MIGRATION: SQLite → PostgreSQL")
    print("=" * 60)
    
    # Step 1: Connect to databases
    print("\n📡 Step 1: Connecting to databases...")
    sqlite_conn = connect_sqlite()
    pg_conn = connect_postgres()
    
    # Step 2: Analyze SQLite database
    print("\n📊 Step 2: Analyzing SQLite database...")
    sqlite_cursor = sqlite_conn.cursor()
    sqlite_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = [row[0] for row in sqlite_cursor.fetchall() if row[0] != 'sqlite_sequence']
    
    total_rows = 0
    print("   Tables found:")
    for table in existing_tables:
        count = get_row_count(sqlite_conn, table)
        total_rows += count
        print(f"      • {table}: {count:,} rows")
    
    print(f"\n   Total data: {total_rows:,} rows across {len(existing_tables)} tables")
    
    # Step 3: Verify PostgreSQL schema exists
    print("\n🏗️ Step 3: Verifying PostgreSQL schema...")
    pg_cursor = pg_conn.cursor()
    pg_cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    pg_tables = [row[0] for row in pg_cursor.fetchall()]
    
    if len(pg_tables) == 0:
        print("   ⚠️ No tables found in PostgreSQL!")
        print("   Please ensure:")
        print("      1. Line 29 in backend/main.py is uncommented")
        print("      2. Server has been started at least once")
        print("      3. No database connection errors occurred")
        print("\n   Run the server first, then re-run this migration script.")
        return False
    
    print(f"   ✅ Found {len(pg_tables)} tables in PostgreSQL")
    for table in pg_tables:
        print(f"      • {table}")
    
    # Step 4: Migrate data
    print("\n📦 Step 4: Migrating data...")
    total_migrated = 0
    
    for table in existing_tables:
        if table in pg_tables:
            migrated = migrate_table_data(sqlite_conn, pg_conn, table)
            total_migrated += migrated
        else:
            print(f"   ⊘ {table}: Table not in PostgreSQL (skipped)")
    
    print(f"\n   Total migrated: {total_migrated:,} rows")
    
    # Step 5: Verify
    verification_success = verify_migration(sqlite_conn, pg_conn)
    
    # Close connections
    sqlite_conn.close()
    pg_conn.close()
    
    # Final summary
    print("\n" + "=" * 60)
    if verification_success:
        print("✅ MIGRATION COMPLETED SUCCESSFULLY!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Restart your server")
        print("2. Test Angel One integration")
        print("3. Verify all endpoints work with PostgreSQL")
        print("4. (Optional) Backup stocks.db and remove it")
    else:
        print("⚠️ MIGRATION COMPLETED WITH WARNINGS")
        print("=" * 60)
        print("\nSome tables have mismatched row counts.")
        print("Please review the verification output above.")
    
    return verification_success


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️ Migration cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
