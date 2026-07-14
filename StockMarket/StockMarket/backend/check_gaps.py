import requests, json, datetime
r = requests.get('http://127.0.0.1:8000/api/stock-data/intraday?ticker=TCS&interval=5m')
data = r.json()
gaps = []
for i in range(1, len(data)):
    prev = data[i-1]['time']
    curr = data[i]['time']
    diff = curr - prev
    if diff > 300:
        prev_dt = datetime.datetime.fromtimestamp(prev, datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        curr_dt = datetime.datetime.fromtimestamp(curr, datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        if prev_dt.hour >= 15 and curr_dt.hour <= 9:
            continue
        gaps.append((prev_dt.strftime('%Y-%m-%d %H:%M'), curr_dt.strftime('%Y-%m-%d %H:%M'), diff//60))
print(f'Total gaps found: {len(gaps)}')
for g in gaps[:10]: print(f'Gap from {g[0]} to {g[1]} ({g[2]} mins)')
