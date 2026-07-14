import urllib.request
import json

url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())

for item in data:
    if item.get('name') == 'SENSEX' and item.get('exch_seg') == 'BSE':
        print(item)
