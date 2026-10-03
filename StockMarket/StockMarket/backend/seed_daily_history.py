"""
Seed 1-Year Daily (1D) candles for major NSE stocks and indices.
Populates the `candles` table so 52-Week High / Low, Market Internals, and Charts work out of the box.
"""
import sys
import time
import pathlib
from datetime import datetime, timezone, timedelta

# Ensure backend directory is in sys.path
backend_dir = pathlib.Path(__file__).parent.resolve()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import yfinance as yf
import pandas as pd
from database import engine
import models
from sqlalchemy.dialects.postgresql import insert as pg_insert

IST = timezone(timedelta(hours=5, minutes=30))

INDEX_MAP = {
    'SENSEX': '^BSESN',
    'NIFTY': '^NSEI',
    'BANKNIFTY': '^NSEBANK',
    'FINNIFTY': 'NIFTY_FIN_SERVICE.NS',
    'MIDCAP': '^NSEMDCP50',
    'SMALLCAP': '^CNXSC',
    'NIFTY_AUTO': '^CNXAUTO',
    'NIFTY_IT': '^CNXIT',
    'NIFTY_PHARMA': '^CNXPHARMA',
    'NIFTY_FMCG': '^CNXFMCG',
    'NIFTY_METAL': '^CNXMETAL',
    'NIFTY_ENERGY': '^CNXENERGY',
    'NIFTY_MEDIA': '^CNXMEDIA',
    'NIFTY_PSU_BANK': '^CNXPSUBANK',
    'NIFTY_REALTY': '^CNXREALTY',
}

TOP_TICKERS = [
    # Core Indices
    'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCAP', 'SMALLCAP', 'SENSEX',
    'NIFTY_AUTO', 'NIFTY_IT', 'NIFTY_PHARMA', 'NIFTY_FMCG', 'NIFTY_METAL',
    'NIFTY_ENERGY', 'NIFTY_MEDIA', 'NIFTY_PSU_BANK', 'NIFTY_REALTY',
    # NIFTY 50 & Heavyweights
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL', 'SBIN',
    'HINDUNILVR', 'ITC', 'LT', 'KOTAKBANK', 'AXISBANK', 'TATAMOTORS', 'BAJFINANCE',
    'MARUTI', 'SUNPHARMA', 'TITAN', 'ULTRACEMCO', 'ASIANPAINT', 'NTPC', 'ONGC',
    'POWERGRID', 'TATASTEEL', 'M&M', 'JSWSTEEL', 'ADANIENT', 'ADANIPORTS', 'COALINDIA',
    'HCLTECH', 'BAJAJFINSV', 'WIPRO', 'TECHM', 'DRREDDY', 'EICHERMOT', 'GRASIM',
    'CIPLA', 'DIVISLAB', 'APOLLOHOSP', 'HEROMOTOCO', 'BPCL', 'TATACONSUM', 'BRITANNIA',
    'HINDALCO', 'NESTLEIND', 'SBILIFE', 'HDFCLIFE', 'BAJAJ-AUTO', 'INDUSINDBK', 'TRENT',
    # Top Active & F&O Names
    'BEL', 'HAL', 'ZOMATO', 'VBL', 'JIOFIN', 'SIEMENS', 'DLF', 'CHOLAFIN',
    'PIDILITIND', 'ABB', 'VEDL', 'TVSMOTOR', 'SHRIRAMFIN', 'IOC', 'GAIL', 'PFC',
    'RECLTD', 'BANKBARODA', 'PNB', 'CANBK', 'INDIGO', 'DMART', 'MOTHERSON', 'POLYCAB',
    'TORNTPHARM', 'LTIM', 'GODREJCP', 'AMBUJACEM', 'HAVELLS', 'DABUR', 'MAXHEALTH',
    'CUMMINSIND', 'PERSISTENT', 'OBEROIRLTY', 'BOSCHLTD', 'COLPAL', 'MUTHOOTFIN',
    'ASHOKLEY', 'AUROPHARMA', 'BALKRISIND', 'BANDHANBNK', 'BERGEPAINT', 'BHEL', 'BIOCON',
    'CANFINHOME', 'CONCOR', 'FEDERALBNK', 'GMRINFRA', 'IDFCFIRSTB', 'IGL', 'LUPIN',
    'MCDOWELL-N', 'NMDC', 'PAGEIND', 'PETRONET', 'SAIL', 'TATACHEM', 'TATAPOWER', 'UPL',
    'VOLTAS', 'ZEEL'
]

def seed_daily_candles():
    print("=" * 65)
    print(f"Daily Candle Seeder (1D / 1-Year) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total Tickers: {len(TOP_TICKERS)}")
    print("=" * 65)

    total_candles = 0
    start_time = time.time()

    for idx, ticker in enumerate(TOP_TICKERS, 1):
        yf_sym = INDEX_MAP.get(ticker, f"{ticker}.NS")
        print(f"[{idx}/{len(TOP_TICKERS)}] {ticker:<15} (yf={yf_sym})... ", end='', flush=True)

        try:
            df = yf.download(yf_sym, period="1y", interval="1d", progress=False, auto_adjust=True)
            if df is None or df.empty:
                print("No data returned")
                continue

            # Flatten MultiIndex if returned by yfinance
            if isinstance(df.columns, pd.MultiIndex):
                if yf_sym in df.columns.get_level_values(1):
                    df = df.xs(yf_sym, axis=1, level=1)
                else:
                    df.columns = df.columns.get_level_values(0)

            rows_to_upsert = []
            for date_idx, row in df.iterrows():
                try:
                    ts = date_idx.to_pydatetime() if hasattr(date_idx, 'to_pydatetime') else date_idx
                    if hasattr(ts, 'tz') and ts.tz is not None:
                        ts = ts.astimezone(IST).replace(tzinfo=None)
                    else:
                        ts = ts.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)

                    # Normalize 1D timestamp to start of trading day 09:15 IST
                    ts = ts.replace(hour=9, minute=15, second=0, microsecond=0)

                    o = float(row.get('Open') or 0)
                    h = float(row.get('High') or 0)
                    l = float(row.get('Low') or 0)
                    c = float(row.get('Close') or 0)
                    v = int(row.get('Volume') or 0)

                    if o <= 0 or c <= 0 or h <= 0 or l <= 0:
                        continue

                    rows_to_upsert.append({
                        'ticker': ticker,
                        'timeframe': '1D',
                        'timestamp': ts,
                        'open': round(o, 2),
                        'high': round(h, 2),
                        'low': round(l, 2),
                        'close': round(c, 2),
                        'volume': v,
                        'is_completed': True,
                    })
                except Exception:
                    continue

            if not rows_to_upsert:
                print("0 valid rows")
                continue

            # Upsert into PostgreSQL candles table
            stmt = pg_insert(models.Candle.__table__).values(rows_to_upsert)
            stmt = stmt.on_conflict_do_update(
                index_elements=['ticker', 'timeframe', 'timestamp'],
                set_={
                    'open': stmt.excluded.open,
                    'high': stmt.excluded.high,
                    'low': stmt.excluded.low,
                    'close': stmt.excluded.close,
                    'volume': stmt.excluded.volume,
                    'is_completed': True,
                }
            )
            with engine.begin() as conn:
                conn.execute(stmt)

            print(f"Upserted {len(rows_to_upsert)} daily candles")
            total_candles += len(rows_to_upsert)
            time.sleep(0.15)

        except Exception as e:
            print(f"ERROR: {e}")

    elapsed = time.time() - start_time
    print("=" * 65)
    print(f"COMPLETE: {total_candles} daily candles seeded in {elapsed:.1f}s")
    print("=" * 65)

if __name__ == "__main__":
    seed_daily_candles()
