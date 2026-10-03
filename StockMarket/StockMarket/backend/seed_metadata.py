"""
Seed stock_metadata table from stocks_list.csv and add core index records.
"""
import sys
import os
import csv
import pathlib

# Ensure backend directory is in sys.path
backend_dir = pathlib.Path(__file__).parent.resolve()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from database import SessionLocal, engine
import models

def seed_metadata():
    db = SessionLocal()
    try:
        # Check if stocks already exist
        existing_count = db.query(models.StockMetadata).count()
        print(f"Current stock_metadata count: {existing_count}")

        # Locate stocks_list.csv
        csv_candidates = [
            backend_dir.parent / "stocks_list.csv",
            backend_dir.parent.parent / "stocks_list.csv",
            backend_dir / "stocks_list.csv",
            pathlib.Path("/opt/leverage/StockMarket/StockMarket/stocks_list.csv"),
            pathlib.Path("/opt/leverage/stocks_list.csv"),
        ]

        csv_file = None
        for cand in csv_candidates:
            if cand.exists():
                csv_file = cand
                break

        if not csv_file:
            print("ERROR: stocks_list.csv not found in candidate paths!")
            return

        print(f"Reading from {csv_file}...")

        # Fetch existing tickers in database to avoid duplicates
        existing_records = db.query(models.StockMetadata.ticker, models.StockMetadata.exchange).all()
        existing_set = set((r[0], r[1] or 'NSE') for r in existing_records)

        # 1. Add core market indices first
        core_indices = [
            {'ticker': 'NIFTY', 'name': 'NIFTY 50', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'BANKNIFTY', 'name': 'BANK NIFTY', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'SENSEX', 'name': 'BSE SENSEX', 'exchange': 'BSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'FINNIFTY', 'name': 'NIFTY FINANCIAL SERVICES', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'MIDCAP', 'name': 'NIFTY MIDCAP 50', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'SMALLCAP', 'name': 'NIFTY SMALLCAP 100', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_AUTO', 'name': 'NIFTY AUTO', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_IT', 'name': 'NIFTY IT', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_PHARMA', 'name': 'NIFTY PHARMA', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_FMCG', 'name': 'NIFTY FMCG', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_METAL', 'name': 'NIFTY METAL', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_ENERGY', 'name': 'NIFTY ENERGY', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_MEDIA', 'name': 'NIFTY MEDIA', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_PSU_BANK', 'name': 'NIFTY PSU BANK', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
            {'ticker': 'NIFTY_REALTY', 'name': 'NIFTY REALTY', 'exchange': 'NSE', 'logo': 'https://s3-symbol-logo.tradingview.com/country/IN.svg'},
        ]

        batch = []
        for idx in core_indices:
            key = (idx['ticker'], idx['exchange'])
            if key not in existing_set:
                stock = models.StockMetadata(
                    ticker=idx['ticker'],
                    name=idx['name'],
                    exchange=idx['exchange'],
                    logo=idx['logo'],
                    base_price=0.0,
                    is_active=True,
                    is_premium=False
                )
                batch.append(stock)
                existing_set.add(key)

        # 2. Add stocks from CSV
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = (row.get('ticker') or '').strip()
                if not ticker:
                    continue
                exchange = (row.get('exchange') or 'NSE').strip()
                name = (row.get('name') or ticker).strip()
                logo = (row.get('logo') or '').strip() or None
                base_price_raw = row.get('base_price')
                try:
                    base_price = float(base_price_raw) if base_price_raw else 0.0
                except (ValueError, TypeError):
                    base_price = 0.0

                key = (ticker, exchange)
                if key not in existing_set:
                    stock = models.StockMetadata(
                        ticker=ticker,
                        name=name,
                        exchange=exchange,
                        logo=logo,
                        base_price=base_price,
                        is_active=True,
                        is_premium=False
                    )
                    batch.append(stock)
                    existing_set.add(key)

                    if len(batch) >= 1000:
                        db.add_all(batch)
                        db.commit()
                        batch = []

        if batch:
            db.add_all(batch)
            db.commit()

        total = db.query(models.StockMetadata).count()
        print(f"✓ Seeding complete! Total stocks in stock_metadata: {total}")

    except Exception as e:
        print(f"Error seeding metadata: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_metadata()
