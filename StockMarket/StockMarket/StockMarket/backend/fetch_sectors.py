"""Batch sector data fetcher for all NSE stocks.
Reads existing sector_data.json, fetches missing sector info from yfinance, and saves back.
Usage: python fetch_sectors.py
"""
import json
import os
import time
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECTOR_DATA_PATH = os.path.join(BASE_DIR, 'sector_data.json')

def load_existing():
    try:
        with open(SECTOR_DATA_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def get_all_tickers_from_db():
    """Get all unique stock tickers from PostgreSQL database."""
    try:
        sys.path.insert(0, BASE_DIR)
        from database import SessionLocal
        from models import StockMetadata
        db = SessionLocal()
        rows = db.query(StockMetadata.ticker).all()
        db.close()
        return list(set(r[0].replace('.NS', '').replace('.BO', '').upper() for r in rows if r[0]))
    except Exception as e:
        print(f"[Sector Fetch] DB unavailable: {e}")
        return None

def get_all_tickers_from_file():
    """Fallback: read tickers from a text file (one ticker per line)."""
    for p in [os.path.join(BASE_DIR, 'tickers.txt'), os.path.join(BASE_DIR, '..', 'tickers.txt')]:
        if os.path.exists(p):
            with open(p) as f:
                return [t.strip().upper() for t in f if t.strip()]
    return None

def fetch_sector_yf(ticker):
    """Fetch sector for a single ticker from yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(f"{ticker}.NS")
        info = t.info
        sector = info.get('sector', '')
        if sector:
            return sector.strip()
    except Exception:
        pass
    return None

def main():
    print("=== Sector Data Fetcher ===")
    existing = load_existing()
    print(f"Existing mappings: {len(existing)}")

    tickers = get_all_tickers_from_db()
    if tickers is None:
        tickers = get_all_tickers_from_file()

    if tickers is None:
        print("No ticker source available. Create a 'tickers.txt' file (one ticker per line) or start the backend.")
        return

    print(f"Total tickers to check: {len(tickers)}")
    missing = [t for t in tickers if t not in existing and t]
    print(f"Missing sectors: {len(missing)}")

    if not missing:
        print("All tickers already have sector data. Nothing to do.")
        return

    found = 0
    start = time.time()
    for i, t in enumerate(missing):
        sector = fetch_sector_yf(t)
        if sector:
            existing[t] = sector
            found += 1
        if (i + 1) % 50 == 0 or sector:
            elapsed = time.time() - start
            print(f"  [{i+1}/{len(missing)}] found={found} total={len(existing)} elapsed={elapsed:.0f}s last={t}->{sector or '?'}")

    with open(SECTOR_DATA_PATH, 'w') as f:
        json.dump(existing, f, indent=2)
    print(f"\nDone. Total mappings: {len(existing)}. Newly found: {found}.")
    print(f"File saved: {SECTOR_DATA_PATH}")

if __name__ == '__main__':
    main()
