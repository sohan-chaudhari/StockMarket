import os
import re

backend_dir = r"c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend"
files = [os.path.join(backend_dir, f) for f in os.listdir(backend_dir) if f.endswith('.py')]

target = "live-prices"
found = False

for file_path in files:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if target in content:
                print(f"Found '{target}' in {file_path}")
                # Print lines around the match
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if target in line:
                        start = max(0, i - 10)
                        end = min(len(lines), i + 35)
                        print(f"--- Lines {start} to {end} in {os.path.basename(file_path)} ---")
                        for idx in range(start, end):
                            print(f"{idx+1}: {lines[idx]}")
                found = True
    except Exception as e:
        print(f"Error reading {file_path}: {e}")

if not found:
    print(f"Could not find '{target}' in any python file in backend.")
