from database import engine
from sqlalchemy import text

def list_tables_and_fks():
    with engine.connect() as connection:
        print("\n--- Tables ---")
        result = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
        for row in result:
            print(f" - {row[0]}")
            
        print("\n--- Foreign Keys referencing users ---")
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
        WHERE tc.constraint_type = 'FOREIGN KEY';
        """
        result = connection.execute(text(sql))
        for row in result:
             print(f" {row.table_name}.{row.column_name} -> {row.foreign_table_name}.{row.foreign_column_name} (ON DELETE {row.delete_rule})")

if __name__ == "__main__":
    list_tables_and_fks()
