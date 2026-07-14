import urllib.request
import json

url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())

sensex_tokens = [item for item in data if item.get('name') == 'SENSEX' or item.get('symbol') == 'SENSEX']
for st in sensex_tokens:
    print(st.get('token'), st.get('symbol'), st.get('name'), st.get('exch_seg'))
