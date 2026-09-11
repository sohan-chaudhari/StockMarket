"""
Phase B: Backfill candles(1D) from stock_data
==============================================
One-time idempotent migration that copies daily OHLCV from the legacy
stock_data table into candles(timeframe='1D').

Safety guarantees:
- ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING: existing candle rows
  are never overwritten. Candles written by the daily prefill / live WS take
  priority.
- Runs in batches of BATCH_SIZE rows, committing after each batch. Safe to
  interrupt and re-run; already-migrated rows are skipped.
- stock_data is never modified or deleted.
- Migrated rows are tagged data_source='YFINANCE', is_backfilled=True so they
  are clearly distinguishable from live-written candles.

Timestamp normalisation:
  stock_data.date (Python date) → midnight of that date stored as a
  timezone-naive datetime in candles.timestamp. This matches what
  _daily_prefill_all and _daily_market_close_sync already write, and what
  chart_service._stored_to_dict expects (treats as IST midnight).

Run:
    cd backend
    python scripts/backfill_1d_candles.py

"""

import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathlib
from datetime import datetime
from sqlalchemy import create_engine, text

# ── DB connection — reuse the same URL the app uses ────────────────────────────
from database import SQLALCHEMY_DATABASE_URL as DB_URL

BATCH_SIZE = 5000


def run():
    engine = create_engine(DB_URL, pool_pre_ping=True)

    with engine.connect() as conn:
        # ── PRE-FLIGHT ────────────────────────────────────────────────────────
        print("=" * 70)
        print("PHASE B: candles(1D) backfill — PRE-FLIGHT REPORT")
        print("=" * 70)

        sd_total = conn.execute(text("SELECT COUNT(*) FROM stock_data")).scalar()
        sd_tickers = conn.execute(text("SELECT COUNT(DISTINCT ticker) FROM stock_data")).scalar()
        sd_min = conn.execute(text("SELECT MIN(date) FROM stock_data")).scalar()
        sd_max = conn.execute(text("SELECT MAX(date) FROM stock_data")).scalar()
        sd_good = conn.execute(text(
            "SELECT COUNT(*) FROM stock_data WHERE open > 0 AND close > 0"
        )).scalar()

        c1d_total = conn.execute(text(
            "SELECT COUNT(*) FROM candles WHERE timeframe='1D'"
        )).scalar()
        c1d_tickers = conn.execute(text(
            "SELECT COUNT(DISTINCT ticker) FROM candles WHERE timeframe='1D'"
        )).scalar()

        print(f"\nstock_data:")
        print(f"  Total rows:          {sd_total:,}")
        print(f"  Distinct tickers:    {sd_tickers:,}")
        print(f"  Date range:          {sd_min} -> {sd_max}")
        print(f"  Rows with OHLCV > 0: {sd_good:,}  (migration candidates)")

        print(f"\ncandles[1D] (before migration):")
        print(f"  Total rows:          {c1d_total:,}")
        print(f"  Distinct tickers:    {c1d_tickers:,}")

        print(f"\nExpected migration rows: {sd_good:,}")
        print(f"(ON CONFLICT DO NOTHING means already-migrated rows are skipped)")

        if sd_good == 0:
            print("\nNothing to migrate. Exiting.")
            return

        print("\n" + "=" * 70)
        print("Starting migration...")
        print("=" * 70)

    # ── MIGRATION ─────────────────────────────────────────────────────────────
    with engine.connect() as conn:
        # Fetch all migrateable rows in chunks using OFFSET.
        # We use raw SQL for performance — SQLAlchemy ORM would load the
        # entire stock_data table into Python objects.
        offset = 0
        total_inserted = 0
        total_skipped = 0
        t0 = time.time()

        while True:
            rows = conn.execute(text("""
                SELECT ticker, date, open, high, low, close,
                       COALESCE(volume, 0) AS volume
                FROM   stock_data
                WHERE  open > 0 AND close > 0
                ORDER  BY ticker, date
                LIMIT  :lim OFFSET :off
            """), {"lim": BATCH_SIZE, "off": offset}).fetchall()

            if not rows:
                break

            # Build batch insert values
            params = []
            for r in rows:
                # date → midnight of that date, stored as IST-naive datetime
                ts = datetime(r.date.year, r.date.month, r.date.day, 0, 0, 0)
                h = max(r.open, r.high, r.close)
                l = min(r.open, r.low, r.close)
                params.append({
                    "ticker": r.ticker,
                    "ts":     ts,
                    "o":      float(r.open),
                    "h":      float(h),
                    "l":      float(l),
                    "c":      float(r.close),
                    "v":      int(r.volume),
                })

            # ON CONFLICT DO NOTHING: existing candle rows (from live WS,
            # prefill, or a previous migration run) are never overwritten.
            result = conn.execute(text("""
                INSERT INTO candles
                    (ticker, timeframe, timestamp, open, high, low, close,
                     volume, is_completed, data_source, is_backfilled)
                SELECT
                    v.ticker, '1D', v.ts, v.o, v.h, v.l, v.c, v.v,
                    true, 'YFINANCE', true
                FROM   (VALUES
                    (:ticker, CAST(:ts AS TIMESTAMP), :o, :h, :l, :c, :v)
                ) AS v(ticker, ts, o, h, l, c, v)
                ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
            """), params)
            conn.commit()

            batch_inserted = result.rowcount
            batch_skipped  = len(params) - batch_inserted
            total_inserted += batch_inserted
            total_skipped  += batch_skipped
            offset += len(rows)

            elapsed = time.time() - t0
            rate = offset / elapsed if elapsed > 0 else 0
            print(f"  Processed {offset:,} / {sd_good:,} rows "
                  f"({100*offset/sd_good:.1f}%)  "
                  f"inserted={total_inserted:,}  skipped={total_skipped:,}  "
                  f"rate={rate:.0f} rows/s")

    # ── POST-FLIGHT ───────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("POST-FLIGHT VERIFICATION")
    print("=" * 70)

    with engine.connect() as conn:
        c1d_after = conn.execute(text(
            "SELECT COUNT(*) FROM candles WHERE timeframe='1D'"
        )).scalar()
        c1d_tickers_after = conn.execute(text(
            "SELECT COUNT(DISTINCT ticker) FROM candles WHERE timeframe='1D'"
        )).scalar()
        c1d_min = conn.execute(text(
            "SELECT MIN(timestamp) FROM candles WHERE timeframe='1D'"
        )).scalar()
        c1d_max = conn.execute(text(
            "SELECT MAX(timestamp) FROM candles WHERE timeframe='1D'"
        )).scalar()

        print(f"\ncandles[1D] (after migration):")
        print(f"  Total rows:       {c1d_after:,}  (was {c1d_total:,}, delta +{c1d_after - c1d_total:,})")
        print(f"  Distinct tickers: {c1d_tickers_after:,}  (was {c1d_tickers:,})")
        print(f"  Date range:       {c1d_min} -> {c1d_max}")

        # Verify no duplicate rows (uniqueness)
        dup = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT ticker, timestamp, COUNT(*)
                FROM   candles
                WHERE  timeframe = '1D'
                GROUP  BY ticker, timestamp
                HAVING COUNT(*) > 1
            ) t
        """)).scalar()
        print(f"\n  Duplicate (ticker, timestamp) pairs: {dup}  {'✓ NONE' if dup == 0 else '⚠ PROBLEM'}")

        # Timestamp normalisation check: all 1D timestamps should be midnight (00:00:00)
        non_midnight = conn.execute(text("""
            SELECT COUNT(*)
            FROM   candles
            WHERE  timeframe = '1D'
              AND  EXTRACT(HOUR FROM timestamp) != 0
        """)).scalar()
        print(f"  Non-midnight timestamps: {non_midnight}  {'✓ NONE' if non_midnight == 0 else '⚠ PROBLEM'}")

        # Ticker coverage: compare with stock_data
        only_in_sd = conn.execute(text("""
            SELECT COUNT(DISTINCT ticker) FROM stock_data
            WHERE  ticker NOT IN (
                SELECT DISTINCT ticker FROM candles WHERE timeframe='1D'
            )
        """)).scalar()
        print(f"  Tickers in stock_data but NOT in candles[1D]: {only_in_sd}  {'✓ ZERO' if only_in_sd == 0 else f'⚠ {only_in_sd} tickers missing'}")

        # Random OHLCV sample comparison
        print(f"\n  Random OHLCV cross-check (5 rows):")
        sample = conn.execute(text("""
            SELECT s.ticker, s.date, s.close AS sd_close,
                   c.close                   AS c1d_close,
                   ABS(s.close - c.close)    AS diff
            FROM   stock_data s
            JOIN   candles c ON c.ticker = s.ticker
                             AND c.timeframe = '1D'
                             AND c.timestamp::date = s.date
            WHERE  s.open > 0
            ORDER  BY random()
            LIMIT  5
        """)).fetchall()
        for r in sample:
            status = "✓" if r.diff < 0.01 else "⚠ MISMATCH"
            print(f"    {r.ticker} {r.date}  sd={r.sd_close:.2f}  c1d={r.c1d_close:.2f}  diff={r.diff:.4f}  {status}")

    print("\n" + "=" * 70)
    print(f"Migration complete: {total_inserted:,} new rows inserted, {total_skipped:,} already existed.")
    elapsed = time.time() - t0
    print(f"Total time: {elapsed:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    run()
