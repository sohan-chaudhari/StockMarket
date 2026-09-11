import json

with open('instruments_cache.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

for item in data:
    if item.get('name') == 'NIFTY 50' or item.get('symbol') == 'NIFTY 50':
        print(item)
