from database import engine
from sqlalchemy import text

def list_fks():
    sql = """
    SELECT
        tc.table_schema, 
        tc.constraint_name, 
        tc.table_name, 
        kcu.column_name, 
        ccu.table_name AS foreign_table_name,
        ccu.column_name AS foreign_column_name 
    FROM 
        information_schema.table_constraints AS tc 
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
          AND ccu.table_schema = tc.table_schema
    WHERE 
        tc.constraint_type = 'FOREIGN KEY' 
        AND ccu.table_name = 'users';
    """
    
    with engine.connect() as connection:
        result = connection.execute(text(sql))
        print(f"{'Table':<20} | {'Constraint':<30} | {'Column':<15}")
        print("-" * 70)
        constraints = []
        for row in result:
            print(f"{row.table_name:<20} | {row.constraint_name:<30} | {row.column_name:<15}")
            constraints.append(row)
            
        return constraints

if __name__ == "__main__":
    list_fks()
