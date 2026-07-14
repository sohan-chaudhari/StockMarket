import sqlite3
conn = sqlite3.connect('stocks.db')
cursor = conn.cursor()
tickers = ('NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL')
query = f"SELECT ticker, date, open, close FROM stock_data WHERE ticker IN {tickers} ORDER BY date DESC LIMIT 50"
cursor.execute(query)
rows = cursor.fetchall()
for row in rows:
    print(row)
conn.close()
