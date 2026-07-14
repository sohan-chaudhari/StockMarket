"""Test the fixed _fetch_yfinance_intraday for 30m."""
import sys
sys.path.insert(0, '.')

import yfinance as yf
import pandas as pd
from datetime import timezone, timedelta
from database import SessionLocal, get_ist_now
from models import Candle
from aggregator import snap_to_nse_session, fix_ohlc

IST = timezone(timedelta(hours=5, minutes=30))

def _safe_float(v):
    try:
        f = float(v)
        return None if (f != f) else f
    except: return None

def _safe_int(v):
    try: return int(v)
    except: return 0

def _epoch_to_ist_dt(epoch_secs):
    return __import__('datetime').datetime.fromtimestamp(epoch_secs, tz=IST).replace(tzinfo=None)

# Simulate the fixed _fetch_yfinance_intraday
yf_ticker = "^BSESN"
interval = "30m"
period = "1mo"

print(f"Downloading {yf_ticker} period={period} interval={interval}...")
yf_int = yf.download(yf_ticker, period=period, interval=interval, progress=False, auto_adjust=True)
print(f"Downloaded {len(yf_int)} rows. Columns type: {type(yf_int.columns)}")
print(f"First 2 columns: {list(yf_int.columns[:2])}")

# Apply MultiIndex fix
if isinstance(yf_int.columns, pd.MultiIndex):
    print("MultiIndex detected - flattening...")
    if yf_ticker in yf_int.columns.get_level_values(1):
        yf_int = yf_int.xs(yf_ticker, axis=1, level=1)
        print(f"After xs: columns={list(yf_int.columns)}")
    else:
        yf_int.columns = yf_int.columns.get_level_values(0)
        print(f"After level-0 drop: columns={list(yf_int.columns)}")

# Test first 5 rows
print("\nFirst 5 rows after fix:")
good = 0
bad = 0
for i, (idx, row) in enumerate(yf_int.iterrows()):
    o = _safe_float(row.get('Open'))
    h = _safe_float(row.get('High'))
    l = _safe_float(row.get('Low'))
    c = _safe_float(row.get('Close'))
    v = _safe_int(row.get('Volume'))
    if o is None and h is None and l is None and c is None:
        bad += 1
        if i < 5: print(f"  Row {i}: SKIP (all None)")
        continue
    o = o or 0.0; h = h or 0.0; l = l or 0.0; c = c or 0.0
    o, h, l, c = fix_ohlc(o, h, l, c)
    if o <= 0 or c <= 0:
        bad += 1
        if i < 5: print(f"  Row {i}: SKIP (zero price)")
        continue
    good += 1
    if i < 5: print(f"  Row {i}: {idx} -> O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} V={v}")

print(f"\nTotal: {good} good rows, {bad} skipped rows")
print("Fix WORKS" if good > 0 else "Fix FAILED - no good rows")
