import requests

r = requests.get('http://127.0.0.1:8000/api/top-9-history')
data = r.json()

print('API Response for NIFTY:')
for d in data['NIFTY']:
    print(f'  {d["day"]}')

print('\nChecking if backend fix is applied:')
if 'Feb 02' in [d['day'] for d in data['NIFTY']]:
    print('  ✗ Backend still returning "Feb 02" - fix NOT applied')
elif 'Feb 2' in [d['day'] for d in data['NIFTY']]:
    print('  ✓ Backend returning "Feb 2" - fix IS applied')
