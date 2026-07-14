import json
with open('OpenAPIScripMaster.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

for item in data:
    if item.get('symbol') == 'SENSEX' and item.get('exch_seg') == 'BSE':
        print(item)
