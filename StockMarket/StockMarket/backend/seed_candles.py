"""
Bulk seed intraday candles — v2 with proper upsert and correct yfinance limits.
Uses PostgreSQL ON CONFLICT DO UPDATE to safely re-run without errors.
"""
import sys, time
sys.path.insert(0, '.')

import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
from database import SessionLocal, engine
import models
from aggregator import snap_to_nse_session, fix_ohlc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text

IST = timezone(timedelta(hours=5, minutes=30))

def safe_float(v):
    try:
        f = float(v)
        return None if (f != f) else f
    except: return None

def safe_int(v):
    try: return int(v)
    except: return 0

def epoch_to_ist_dt(epoch_secs):
    return datetime.fromtimestamp(epoch_secs, tz=IST).replace(tzinfo=None)

INDEX_MAP = {
    'SENSEX': '^BSESN', 'NIFTY': '^NSEI', 'BANKNIFTY': '^NSEBANK',
    'NIFTYMIDCAP100': '^CNX100', 'MIDCAP': '^NSEMDCP50', 'SMALLCAP': '^CNXSC',
}

def yf_ticker_for(clean_ticker):
    return INDEX_MAP.get(clean_ticker, clean_ticker + '.NS')

def seed_ticker_interval(clean_ticker, interval, period):
    yf_sym = yf_ticker_for(clean_ticker)
    print(f"  {clean_ticker} / {interval} (yf={yf_sym}, period={period})", end='', flush=True)
    try:
        df = yf.download(yf_sym, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            print(" -> no data")
            return 0

        # Flatten MultiIndex (yfinance v0.2+ returns MultiIndex for single tickers)
        if isinstance(df.columns, pd.MultiIndex):
            if yf_sym in df.columns.get_level_values(1):
                df = df.xs(yf_sym, axis=1, level=1)
            else:
                df.columns = df.columns.get_level_values(0)

        bucket_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60}.get(interval, 5)
        rows_to_upsert = {}  # keyed by snapped_dt to deduplicate

        for idx, row in df.iterrows():
            try:
                ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                if hasattr(idx, 'tz') and idx.tz is not None:
                    ts = ts.astimezone(IST).replace(tzinfo=None)
                else:
                    ts = ts.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)

                epoch = int(ts.replace(tzinfo=IST).timestamp())
                snapped_epoch = snap_to_nse_session(epoch, bucket_min)
                snapped_dt = epoch_to_ist_dt(snapped_epoch)

                o = safe_float(row.get('Open'))
                h = safe_float(row.get('High'))
                l = safe_float(row.get('Low'))
                c = safe_float(row.get('Close'))
                v = safe_int(row.get('Volume'))

                if o is None and h is None and l is None and c is None:
                    continue
                o = o or 0.0; h = h or 0.0; l = l or 0.0; c = c or 0.0
                o, h, l, c = fix_ohlc(o, h, l, c)
                if o <= 0 or c <= 0:
                    continue

                # Deduplicate: if two yfinance rows snap to the same bucket, merge them
                key = snapped_dt
                if key in rows_to_upsert:
                    existing = rows_to_upsert[key]
                    rows_to_upsert[key] = {
                        'ticker': clean_ticker, 'timeframe': interval, 'timestamp': snapped_dt,
                        'open': existing['open'],
                        'high': max(existing['high'], h),
                        'low': min(existing['low'], l),
                        'close': c, 'volume': existing['volume'] + v,
                        'is_completed': True,
                    }
                else:
                    rows_to_upsert[key] = {
                        'ticker': clean_ticker, 'timeframe': interval, 'timestamp': snapped_dt,
                        'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
                        'is_completed': True,
                    }
            except Exception:
                continue

        if not rows_to_upsert:
            print(" -> 0 valid rows")
            return 0

        # Bulk upsert with ON CONFLICT DO UPDATE
        stmt = pg_insert(models.Candle.__table__).values(list(rows_to_upsert.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=['ticker', 'timeframe', 'timestamp'],
            set_={
                'high': stmt.excluded.high,
                'low': stmt.excluded.low,
                'close': stmt.excluded.close,
                'volume': stmt.excluded.volume,
                'is_completed': True,
            }
        )
        with engine.begin() as conn:
            conn.execute(stmt)

        print(f" -> upserted {len(rows_to_upsert)} candles")
        return len(rows_to_upsert)

    except Exception as e:
        print(f" -> ERROR: {str(e)[:120]}")
        return 0

# ── Targets ────────────────────────────────────────────────────────────
TARGETS = [
    'SENSEX', 'NIFTY', 'BANKNIFTY',
    'RELIANCE', 'HDFCBANK', 'INFY', 'TCS', 'ICICIBANK', 'SBIN',
    'AXISBANK', 'TATAMOTORS', 'BAJFINANCE', 'WIPRO', 'HINDUNILVR',
    'MARUTI', 'LT', 'KOTAKBANK', 'NTPC', 'ONGC', 'BHARTIARTL',
    'ADANIENT', 'POWERGRID', 'DRREDDY', 'COALINDIA', 'BEL',
    'TATASTEEL', 'ITC', 'SUNPHARMA', 'ULTRACEMCO', 'TITAN',
]

# yfinance period limits: 1m=7d, 5m=60d, 15m=60d, 30m=60d, 1h=730d
INTERVALS = [
    ('30m', '60d'),
    ('1h',  '60d'),
    ('15m', '60d'),
    ('5m',  '5d'),
]

print("=" * 65)
print(f"Bulk Candle Seeder v2 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Targets: {len(TARGETS)} tickers × {len(INTERVALS)} timeframes")
print("=" * 65)

start = time.time()
grand = 0
errors = []
for ticker in TARGETS:
    print(f"\n[{ticker}]")
    for interval, period in INTERVALS:
        count = seed_ticker_interval(ticker, interval, period)
        grand += count
        time.sleep(0.3)

print(f"\n{'='*65}")
print(f"COMPLETE. {grand} candles upserted in {time.time()-start:.1f}s")
print("=" * 65)
