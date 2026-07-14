import re
import os

main_path = r"c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend\main.py"
with open(main_path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: ModuleNotFoundError
if "sys.path.append(os.path.dirname(os.path.abspath(__file__)))" not in content:
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line.startswith("import logging"):
            lines.insert(i, "import sys\nimport os\nsys.path.append(os.path.dirname(os.path.abspath(__file__)))\n")
            break
    content = '\n'.join(lines)

# Write back
with open(main_path, "w", encoding="utf-8") as f:
    f.write(content)
print("main.py fixes applied successfully.")
