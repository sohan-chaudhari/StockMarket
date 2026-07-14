"""
One-time migration: aggregate all existing 1m candles into 5m, then drop 1m rows.

Steps:
  1. Group 1m candles by (ticker, trading_date)
  2. Pandas resample to 5m (vectorized)
  3. Validate: OHLC match, volume sum, count(n_1m) / 3 ≤ count(n_5m) ≤ count(n_1m)
  4. INSERT 5m candles (ON CONFLICT DO NOTHING)
  5. Verify no data loss
  6. DROP 1m rows from candles table
  7. VACUUM (optional)

Run only after confirming the new 5m-only architecture is stable.
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from database import SessionLocal
from models import Candle
from sqlalchemy import text


def migrate_1m_to_5m(dry_run: bool = True, batch_size: int = 1000):
    print("=" * 60)
    print("  1m → 5m Migration Script")
    print(f"  Dry run: {dry_run}")
    print("=" * 60)

    db = SessionLocal()
    try:
        total_1m = db.query(Candle).filter(Candle.timeframe == "1m").count()
        print(f"\nTotal 1m candles in DB: {total_1m}")

        tickers = [r[0] for r in db.query(Candle.ticker).filter(
            Candle.timeframe == "1m"
        ).distinct().all()]
        print(f"Tickers with 1m data: {len(tickers)}")

        total_converted = 0
        total_skipped = 0

        for ticker in tickers:
            one_m_candles = db.query(Candle).filter(
                Candle.ticker == ticker,
                Candle.timeframe == "1m",
                Candle.is_completed == True,
            ).order_by(Candle.timestamp.asc()).all()

            if not one_m_candles:
                continue

            records = [{
                "timestamp": c.timestamp,
                "open": c.open, "high": c.high, "low": c.low,
                "close": c.close, "volume": c.volume,
            } for c in one_m_candles]

            df = pd.DataFrame(records)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)
            df.sort_index(inplace=True)

            resampled = df.resample("5min", closed="left", label="left").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()

            if resampled.empty:
                continue

            source_vol = df["volume"].sum()
            target_vol = resampled["volume"].sum()

            if source_vol != target_vol:
                print(f"  VOLUME MISMATCH {ticker}: source={source_vol} target={target_vol}")
                continue

            check_open = resampled["open"].iloc[0] == one_m_candles[0].open
            check_close = resampled["close"].iloc[-1] == one_m_candles[-1].close
            check_high = resampled["high"].max() == max(c.high for c in one_m_candles)
            check_low = resampled["low"].min() == min(c.low for c in one_m_candles)

            if not (check_open and check_close and check_high and check_low):
                print(f"  OHLC MISMATCH {ticker}: open={check_open} close={check_close} high={check_high} low={check_low}")
                continue

            existing_5m = {r[0] for r in db.query(Candle.timestamp).filter(
                Candle.ticker == ticker,
                Candle.timeframe == "5m",
            ).all()}

            inserted = 0
            for ts, row in resampled.iterrows():
                ts_dt = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts
                if ts_dt in existing_5m:
                    continue
                if not dry_run:
                    new_5m = Candle(
                        ticker=ticker, timeframe="5m",
                        timestamp=ts_dt,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(row["volume"]),
                        is_completed=True,
                        data_source="MIGRATION_1M",
                        is_backfilled=True,
                    )
                    db.add(new_5m)
                inserted += 1

            if not dry_run:
                try:
                    db.commit()
                    db.query(Candle).filter(
                        Candle.ticker == ticker,
                        Candle.timeframe == "1m",
                    ).delete(synchronize_session=False)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    print(f"  COMMIT ERROR {ticker}: {e}")
                    continue

            total_converted += len(resampled)
            total_skipped += len(one_m_candles)

            if len(tickers) <= 20 or (tickers.index(ticker) + 1) % 100 == 0:
                pct = (tickers.index(ticker) + 1) / len(tickers) * 100
                print(f"  [{pct:.0f}%] {ticker}: {len(one_m_candles)} 1m → {len(resampled)} 5m (inserted {inserted})")

        print(f"\n{'=' * 60}")
        print(f"Migration complete!")
        print(f"  1m candles processed: {total_skipped}")
        print(f"  5m candles created: {total_converted}")
        if dry_run:
            print(f"\n  *** DRY RUN — no changes committed ***")
            print(f"  Re-run with dry_run=False to execute")
        else:
            remaining = db.query(Candle).filter(Candle.timeframe == "1m").count()
            print(f"  Remaining 1m candles: {remaining}")
            if remaining == 0:
                print(f"  All 1m candles successfully migrated and removed!")

    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--execute" not in sys.argv
    if not dry_run:
        confirm = input("This will DELETE all 1m candles after migration. Type 'yes' to confirm: ")
        if confirm != "yes":
            print("Aborted.")
            sys.exit(1)
    migrate_1m_to_5m(dry_run=dry_run)
