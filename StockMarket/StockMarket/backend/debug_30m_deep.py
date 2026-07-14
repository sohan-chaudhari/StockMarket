"""Test yfinance 30m download and the full endpoint flow."""
import sys
sys.path.insert(0, '.')

# Test 1: What does yfinance return for 30m?
import yfinance as yf
print("Testing yfinance 30m download...")
try:
    df = yf.download("^BSESN", period="1mo", interval="30m", progress=False, auto_adjust=True)
    print(f"SENSEX 30m: {len(df)} rows")
    if not df.empty:
        print(f"  First: {df.index[0]}, Last: {df.index[-1]}")
        print(f"  Columns: {list(df.columns)}")
        print(f"  First row: {df.iloc[0].to_dict()}")
except Exception as e:
    print(f"SENSEX 30m ERROR: {e}")
    import traceback; traceback.print_exc()

print()
# Test 2: What does the full _fetch_yfinance_intraday do?
from database import SessionLocal
from aggregator import snap_to_nse_session, is_trading_day, fix_ohlc
from datetime import datetime, timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))

def _safe_float(v):
    try:
        f = float(v)
        return None if (f != f) else f  # NaN check
    except: return None

def _safe_int(v):
    try: return int(v)
    except: return 0

def _epoch_to_ist_dt(epoch_secs):
    return datetime.fromtimestamp(epoch_secs, tz=IST).replace(tzinfo=None)

def _ts_to_epoch(ts):
    if ts is None: return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return int(ts.timestamp())

# Test the actual function inline
db = SessionLocal()
try:
    yf_ticker = "^BSESN"
    interval = "30m"
    period_map = {"1m": "7d", "5m": "5d", "15m": "1mo", "30m": "1mo", "1h": "1mo"}
    yf_period = period_map.get(interval, "5d")
    print(f"Downloading {yf_ticker} period={yf_period} interval={interval}")
    yf_int = yf.download(yf_ticker, period=yf_period, interval=interval, progress=False, auto_adjust=True)
    print(f"Downloaded {len(yf_int)} rows")
    if yf_int is not None and not yf_int.empty:
        bucket_min = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(interval, 5)
        print(f"bucket_min={bucket_min}")
        for i, (idx, row) in enumerate(yf_int.iterrows()):
            if i >= 3: break
            ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            print(f"  Row {i}: idx={idx}, ts type={type(ts)}, tz={getattr(ts, 'tzinfo', 'N/A')}")
            try:
                if hasattr(idx, 'tz') and idx.tz is not None:
                    ts = ts.astimezone(IST).replace(tzinfo=None)
                else:
                    ts = ts.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)
                epoch = int(ts.replace(tzinfo=IST).timestamp())
                snapped = snap_to_nse_session(epoch, bucket_min)
                snapped_dt = _epoch_to_ist_dt(snapped)
                print(f"    ts_ist={ts}, snapped_dt={snapped_dt}")
                o = _safe_float(row.get('Open'))
                h = _safe_float(row.get('High'))
                l = _safe_float(row.get('Low'))
                c = _safe_float(row.get('Close'))
                print(f"    OHLC: {o},{h},{l},{c}")
                o, h, l, c = fix_ohlc(o, h, l, c)
                print(f"    fixed OHLC: {o},{h},{l},{c}")
            except Exception as e:
                print(f"    ERROR: {e}")
                import traceback; traceback.print_exc()
except Exception as e:
    print(f"Full ERROR: {e}")
    import traceback; traceback.print_exc()
finally:
    db.close()
