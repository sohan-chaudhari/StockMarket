backend_file = r"c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend\main.py"
with open(backend_file, 'r', encoding='utf-8') as f:
    content = f.read()

target = "async def fetch_batch_live_data"
if target in content:
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if target in line:
            start = max(0, i - 2)
            end = min(len(lines), i + 45)
            print(f"--- {target} ---")
            for idx in range(start, end):
                print(f"{idx+1}: {lines[idx]}")
else:
    print(f"Could not find '{target}' in main.py")
