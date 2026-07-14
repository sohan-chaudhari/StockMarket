"""
Manual backfill script using direct requests to Yahoo Finance API
Run this once to fill historical data for all major stocks.
Usage: venv/Scripts/python.exe backfill_history.py
"""

import requests
import sys
import os
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal
import models

def fetch_yahoo_history(ticker_ns, start_ts, end_ts):
    """Fetch data directly from Yahoo Finance v8 API"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_ns}"
    params = {
        'period1': start_ts,
        'period2': end_ts,
        'interval': '1d',
        'events': 'history',
        'includeAdjustedClose': 'true',
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 200:
            data = r.json()
            result = data.get('chart', {}).get('result', [])
            if result:
                ts = result[0].get('timestamp', [])
                q = result[0].get('indicators', {}).get('quote', [{}])[0]
                opens = q.get('open', [])
                highs = q.get('high', [])
                lows = q.get('low', [])
                closes = q.get('close', [])
                vols = q.get('volume', [])
                rows = []
                for i, t in enumerate(ts):
                    try:
                        d = date.fromtimestamp(t)
                        o = opens[i] if opens[i] is not None else None
                        h = highs[i] if highs[i] is not None else None
                        l = lows[i] if lows[i] is not None else None
                        c = closes[i] if closes[i] is not None else None
                        v = vols[i] if vols[i] is not None else 0
                        if o and h and l and c:
                            rows.append({'date': d, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': int(v or 0)})
                    except Exception:
                        pass
                return rows
        print(f"  HTTP {r.status_code} for {ticker_ns}")
    except Exception as e:
        print(f"  Request failed: {e}")
    return []

def backfill_ticker(db, ticker, ticker_ns):
    """Backfill full history for a ticker"""
    import calendar
    start_ts = int(datetime(2015, 1, 1).timestamp())
    end_ts = int(datetime.now().timestamp())
    
    print(f"\nFetching {ticker_ns} ({ticker}) from 2015...")
    rows = fetch_yahoo_history(ticker_ns, start_ts, end_ts)
    
    if not rows:
        print(f"  No data for {ticker_ns}")
        return 0
    
    print(f"  Got {len(rows)} rows from Yahoo. Inserting new ones...")
    
    # Get existing dates
    existing = set(d[0] for d in db.query(models.StockData.date).filter(models.StockData.ticker == ticker).all())
    
    new_rows = []
    for r in rows:
        if r['date'] not in existing and r['date'].weekday() < 5:
            new_rows.append({
                'ticker': ticker,
                'date': r['date'],
                'open': round(r['open'], 2),
                'high': round(r['high'], 2),
                'low': round(r['low'], 2),
                'close': round(r['close'], 2),
                'adj_close': round(r['close'], 2),
                'volume': r['volume'],
            })
    
    if new_rows:
        db.bulk_insert_mappings(models.StockData, new_rows)
        db.commit()
        print(f"  [OK] Inserted {len(new_rows)} new records for {ticker}")
    else:
        print(f"  Already up to date for {ticker}")
    
    return len(new_rows)

MAJOR_STOCKS = [
    # Stocks that failed previously due to rate limiting - retry these
    ('BHARTIARTL', 'BHARTIARTL.NS'),
    ('KOTAKBANK', 'KOTAKBANK.NS'),
    ('AXISBANK', 'AXISBANK.NS'),
    ('WIPRO', 'WIPRO.NS'),
    ('TATAMOTORS', 'TATAMOTORS.NS'),
    ('MARUTI', 'MARUTI.NS'),
    ('SUNPHARMA', 'SUNPHARMA.NS'),
    ('DRREDDY', 'DRREDDY.NS'),
    ('NIFTY', '^NSEI'),
    ('BANKNIFTY', '^NSEBANK'),
    ('SENSEX', '^BSESN'),
    # Extra popular stocks
    ('LT', 'LT.NS'),
    ('ADANIENT', 'ADANIENT.NS'),
    ('BAJFINANCE', 'BAJFINANCE.NS'),
    ('HCLTECH', 'HCLTECH.NS'),
]

if __name__ == '__main__':
    db = SessionLocal()
    total = 0
    for ticker, ticker_ns in MAJOR_STOCKS:
        inserted = backfill_ticker(db, ticker, ticker_ns)
        total += inserted
        print(f"  Waiting 3 seconds before next request...")
        time.sleep(3)  # Longer delay to avoid rate limiting
    db.close()
    print(f"\n[DONE] Total {total} new records inserted.")
