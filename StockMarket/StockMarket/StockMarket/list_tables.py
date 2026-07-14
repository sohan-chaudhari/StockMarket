import sqlite3
conn = sqlite3.connect('stocks.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
rows = cursor.fetchall()
for row in rows:
    print(row)
conn.close()
