import sqlite3
conn = sqlite3.connect('stockmarket.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
print([r[0] for r in cur.fetchall()])
