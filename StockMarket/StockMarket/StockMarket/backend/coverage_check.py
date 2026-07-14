"""Comprehensive candle coverage check across all timeframes and tickers."""
import sys
sys.path.insert(0, '.')
from database import SessionLocal, get_ist_now
from models import Candle
from datetime import timedelta
from sqlalchemy import func, text

db = SessionLocal()
ist_now = get_ist_now()

TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h']
CUTOFFS = {'1m': 7, '5m': 10, '15m': 45, '30m': 90, '1h': 120}

# All tickers that have candle data
all_tickers = [r[0] for r in db.query(Candle.ticker).distinct().order_by(Candle.ticker).all()]
print(f"Total unique tickers with candle data: {len(all_tickers)}")
print(f"Current IST: {ist_now}\n")

# Per-timeframe summary
print("=" * 70)
print(f"{'TIMEFRAME':<10} {'TICKERS WITH DATA':<22} {'TOTAL CANDLES':<18} {'CUTOFF'}")
print("=" * 70)
for tf in TIMEFRAMES:
    cutoff = ist_now - timedelta(days=CUTOFFS[tf])
    rows = db.execute(text(
        f"SELECT COUNT(DISTINCT ticker), COUNT(*) FROM candles "
        f"WHERE timeframe='{tf}' AND is_completed=true "
        f"AND timestamp >= '{cutoff.strftime('%Y-%m-%d %H:%M:%S')}'"
    )).fetchone()
    total_rows = db.execute(text(
        f"SELECT COUNT(*) FROM candles WHERE timeframe='{tf}' AND is_completed=true"
    )).fetchone()
    print(f"  {tf:<10} {rows[0]:<22} in-window: {rows[1]:<10} total: {total_rows[0]:<8} (cutoff: {CUTOFFS[tf]}d)")

print()
# Key indices check
INDICES = ['SENSEX', 'NIFTY', 'BANKNIFTY', 'NIFTYMIDCAP100', 'NIFTY_FIN_SERVICE']
print("=" * 70)
print(f"{'TICKER':<22} {'1m':>6} {'5m':>6} {'15m':>6} {'30m':>6} {'1h':>6}  STATUS")
print("=" * 70)

def check_ticker(ticker, db, ist_now):
    results = {}
    for tf in TIMEFRAMES:
        cutoff = ist_now - timedelta(days=CUTOFFS[tf])
        count = db.query(func.count(Candle.id)).filter(
            Candle.ticker == ticker,
            Candle.timeframe == tf,
            Candle.is_completed == True,
            Candle.timestamp >= cutoff.replace(tzinfo=None)
        ).scalar()
        results[tf] = count
    return results

print("--- KEY INDICES ---")
for ticker in INDICES:
    r = check_ticker(ticker, db, ist_now)
    status = "OK" if all(v > 0 for v in r.values()) else ("PARTIAL" if any(v > 0 for v in r.values()) else "NO DATA")
    print(f"  {ticker:<22} {r['1m']:>6} {r['5m']:>6} {r['15m']:>6} {r['30m']:>6} {r['1h']:>6}  [{status}]")

# Top traded stocks
STOCKS = ['RELIANCE', 'HDFCBANK', 'INFY', 'TCS', 'HDFC', 'ICICIBANK', 'SBIN', 'AXISBANK', 'TATAMOTORS', 'BAJFINANCE']
print("\n--- TOP STOCKS ---")
for ticker in STOCKS:
    r = check_ticker(ticker, db, ist_now)
    status = "OK" if all(v > 0 for v in r.values()) else ("PARTIAL" if any(v > 0 for v in r.values()) else "NO DATA")
    print(f"  {ticker:<22} {r['1m']:>6} {r['5m']:>6} {r['15m']:>6} {r['30m']:>6} {r['1h']:>6}  [{status}]")

# Show latest candle timestamp per timeframe for SENSEX
print("\n--- LATEST CANDLE TIMES FOR SENSEX ---")
for tf in TIMEFRAMES:
    latest = db.query(func.max(Candle.timestamp)).filter(
        Candle.ticker == 'SENSEX', Candle.timeframe == tf, Candle.is_completed == True
    ).scalar()
    print(f"  {tf}: {latest}")

db.close()
print("\n[DONE]")
