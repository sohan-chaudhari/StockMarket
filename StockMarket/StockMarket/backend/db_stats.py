from database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
res = db.execute(text("SELECT relname AS table_name, pg_size_pretty(pg_total_relation_size(relid)) AS total_size, pg_total_relation_size(relid) as bytes, n_live_tup as row_count FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC;")).fetchall()
print('TABLE STATS:')
for r in res: print(f'{r[0]}: {r[1]} ({r[3]} rows, {r[2]} bytes)')
