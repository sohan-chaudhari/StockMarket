"""Script to find line numbers in main.py and rewrite the broken section."""
import re

with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')
print(f'Total lines: {len(lines)}')
markers = {
    'normalize_ticker def': 'def _normalize_ticker',
    'range endpoint': '@app.get("/api/stock-data/range")',
    'yfinance_ticker def': 'def _yfinance_ticker',
    'since is None': 'if since is None:',
    'get_intraday_since': 'def get_intraday_since',
    'STATIC FILES': 'STATIC FILES MOUNT',
    'on_angel_tick': 'def _on_angel_tick',
}
for name, marker in markers.items():
    for i, line in enumerate(lines, 1):
        if marker in line:
            print(f'  {name}: line {i} => {line.strip()[:80]}')
            break
