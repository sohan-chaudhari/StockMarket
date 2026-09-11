import json

with open('instruments_cache.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

for item in data:
    if item.get('token') in ('26000', '26009'):
        print(item)
