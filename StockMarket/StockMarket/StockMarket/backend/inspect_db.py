from database import engine
from sqlalchemy import text

def inspect_db():
    print("Inspecting database tables and foreign keys...")
    with engine.connect() as connection:
        # 1. List all tables
        print("\n--- Tables ---")
        result = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
        tables = [row[0] for row in result]
        for table in tables:
            print(f" - {table}")
        
        # 2. List foreign keys referencing 'users'
        print("\n--- Foreign Keys referencing 'users' ---")
        sql = """
        SELECT
            tc.table_name, 
            kcu.column_name, 
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name,
            rc.delete_rule
        FROM 
            information_schema.table_constraints AS tc 
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
            JOIN information_schema.referential_constraints AS rc
              ON rc.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name='users';
        """
        result = connection.execute(text(sql))
        for row in result:
            print(f" {row.table_name}.{row.column_name} -> {row.foreign_table_name}.{row.foreign_column_name} (ON DELETE {row.delete_rule})")

if __name__ == "__main__":
    inspect_db()
