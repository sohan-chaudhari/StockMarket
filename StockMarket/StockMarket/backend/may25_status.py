"""
May 25 Recovery - Live Status Checker
Run this any time while may25_recovery.py is running to see current progress.

Usage:
    python may25_status.py
"""
import json, pathlib, sys

PROGRESS_FILE = pathlib.Path(__file__).parent / "may25_progress.json"

if not PROGRESS_FILE.exists():
    print("No progress file found. may25_recovery.py has not started yet.")
    sys.exit(0)

d = json.loads(PROGRESS_FILE.read_text())

done  = d["tickers_done"]
total = d["tickers_total"]
bar_w = 40
filled = int(bar_w * done / total) if total else 0
bar = "#" * filled + "." * (bar_w - filled)

print()
print("=" * 60)
print(f"  May 25 Recovery - {d['mode'].upper()}  [{d['phase'].upper()}]")
print("=" * 60)
print(f"  [{bar}] {d['pct_complete']}%")
print(f"  {done:,} / {total:,} tickers  |  ETA: {d['eta'] or 'done'}")
print(f"  Speed: {d['tickers_per_sec']} tickers/sec  |  Elapsed: {d['elapsed_sec']}s")
print()
print(f"  {'Tickers with AngelOne data':<40} {d['with_data']:>8,}")
print(f"  {'Tickers with no data':<40} {d['no_data']:>8,}")
print(f"  {'Tickers permanent error':<40} {d['perm_errors']:>8,}")
print(f"  {'Tickers retryable error':<40} {d['retry_errors']:>8,}")
print()
print(f"  {'Candles from provider (so far)':<40} {d['candles_from_provider']:>8,}")
print(f"  {'Already in DB (will skip)':<40} {d['already_in_db']:>8,}")
print(f"  {'Missing - would insert':<40} {d['to_insert']:>8,}")
if d['mode'] == 'execute':
    print(f"  {'Rows actually inserted':<40} {d['inserted']:>8,}")
print()
print(f"  As of: {d['as_of']}")
print("=" * 60)
print()
