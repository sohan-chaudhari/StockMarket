backend_file = r"c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend\main.py"
with open(backend_file, 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "yf." in line or "yfinance" in line:
        print(f"{i+1}: {line.strip()}")
