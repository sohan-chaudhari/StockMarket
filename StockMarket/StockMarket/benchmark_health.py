import urllib.request
import time

urls = [
    'http://127.0.0.1:8000/',
    'http://127.0.0.1:8000/api/csrf-token',
    'http://127.0.0.1:8000/api/market-movers?cap=all',
    'http://127.0.0.1:8000/api/market-movers?cap=large',
    'http://127.0.0.1:8000/api/market-movers?cap=mid',
    'http://127.0.0.1:8000/api/market-movers?cap=small',
    'http://127.0.0.1:8000/api/watchlist',
    'http://127.0.0.1:8000/logos/RELIANCE.svg'
]

print("--- HEALTH & LATENCY BENCHMARK ---")
for u in urls:
    t0 = time.time()
    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            elapsed = (time.time() - t0) * 1000
            print(f'{u} -> HTTP {resp.status} in {elapsed:.1f}ms')
    except Exception as e:
        print(f'{u} -> ERROR: {e}')
