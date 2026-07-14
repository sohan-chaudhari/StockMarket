from database import engine
from sqlalchemy import text

def check_rules():
    sql = """
    SELECT 
        constraint_name, 
        delete_rule 
    FROM 
        information_schema.referential_constraints 
    WHERE 
        constraint_name IN ('transactions_user_id_fkey', 'watchlist_user_id_fkey');
    """
    
    with engine.connect() as connection:
        result = connection.execute(text(sql))
        print(f"{'Constraint':<30} | {'Delete Rule':<15}")
        print("-" * 50)
        for row in result:
            print(f"{row.constraint_name:<30} | {row.delete_rule:<15}")

if __name__ == "__main__":
    check_rules()
