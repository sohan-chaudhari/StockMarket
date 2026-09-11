#!/usr/bin/env python3
"""
backfill_weekly_monthly.py
==========================
Populates candles(1W) and candles(1M) per the 3-tier retention strategy:

  0 -> 2 years   -> candles timeframe='1D'  (already populated)
  2 -> 5 years   -> candles timeframe='1W'  ← this script fills it
  5+  years     -> candles timeframe='1M'  ← this script fills it

Phase 1 (fast, SQL only):
  Aggregate existing stock_data daily rows into 1W / 1M candles.
  Covers only tickers that already have historical rows in stock_data (~42).

Phase 2 (slow, AngelOne fetch):
  For every active NSE ticker still missing 1W or 1M coverage, fetch
  ONE_DAY history from AngelOne in 365-day chunks, then resample.

Usage:
    python backfill_weekly_monthly.py            # both phases
    python backfill_weekly_monthly.py --phase1   # SQL only (fast, ~seconds)
    python backfill_weekly_monthly.py --phase2   # AngelOne fetch only (hours)
    python backfill_weekly_monthly.py --phase2 --timeframe 1W   # 1W only
    python backfill_weekly_monthly.py --phase2 --timeframe 1M   # 1M only
"""

import sys
import os
import time
import argparse
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal
from sqlalchemy import text as sa_text

# ---------------------------------------------------------------------------
# Retention cutoffs (rolling, recalculated each run)
# ---------------------------------------------------------------------------
TODAY           = date.today()
TWO_YR_CUTOFF   = date(TODAY.year - 2,  TODAY.month, TODAY.day)   # 1D floor  (2024-08-23)
FIVE_YR_CUTOFF  = date(TODAY.year - 5,  TODAY.month, TODAY.day)   # 1W floor  (2021-08-23)
TEN_YR_CUTOFF   = date(TODAY.year - 10, TODAY.month, TODAY.day)   # 1M floor  (2016-08-23)
# Total fetch window: 3yr (1W range) + 5yr (1M range) = 8 years of 1D data


# ===========================================================================
# PHASE 1 — SQL aggregation from stock_data
# ===========================================================================

def phase1_migrate_stock_data() -> None:
    print(f"\n[Phase 1] Migrating stock_data -> candles(1W) and candles(1M)")
    print(f"  1W range : {FIVE_YR_CUTOFF} -> {TWO_YR_CUTOFF}")
    print(f"  1M range : {TEN_YR_CUTOFF}   -> {FIVE_YR_CUTOFF}\n")

    with SessionLocal() as db:
        # ---- 1W ----------------------------------------------------------
        print("  Running 1W aggregation...")
        r1w = db.execute(sa_text("""
            INSERT INTO candles
                (ticker, timeframe, timestamp, open, high, low, close, volume,
                 is_completed, is_backfilled, data_source)
            SELECT
                ticker,
                '1W',
                DATE_TRUNC('week', date)::timestamp,
                (ARRAY_AGG(open  ORDER BY date ASC))[1]   AS open,
                MAX(high)                                  AS high,
                MIN(low)                                   AS low,
                (ARRAY_AGG(close ORDER BY date DESC))[1]  AS close,
                SUM(volume)::bigint                        AS volume,
                TRUE, TRUE, 'SD_AGG'
            FROM stock_data
            WHERE date >= :from_d
              AND date <  :to_d
              AND open  IS NOT NULL
              AND close IS NOT NULL
              AND open  > 0
            GROUP BY ticker, DATE_TRUNC('week', date)
            ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
        """), {"from_d": FIVE_YR_CUTOFF, "to_d": TWO_YR_CUTOFF})
        db.commit()
        print(f"  -> Inserted {r1w.rowcount:,} 1W candles")

        # ---- 1M ----------------------------------------------------------
        print("  Running 1M aggregation...")
        r1m = db.execute(sa_text("""
            INSERT INTO candles
                (ticker, timeframe, timestamp, open, high, low, close, volume,
                 is_completed, is_backfilled, data_source)
            SELECT
                ticker,
                '1M',
                DATE_TRUNC('month', date)::timestamp,
                (ARRAY_AGG(open  ORDER BY date ASC))[1]   AS open,
                MAX(high)                                  AS high,
                MIN(low)                                   AS low,
                (ARRAY_AGG(close ORDER BY date DESC))[1]  AS close,
                SUM(volume)::bigint                        AS volume,
                TRUE, TRUE, 'SD_AGG'
            FROM stock_data
            WHERE date < :cutoff
              AND open  IS NOT NULL
              AND close IS NOT NULL
              AND open  > 0
            GROUP BY ticker, DATE_TRUNC('month', date)
            ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
        """), {"cutoff": FIVE_YR_CUTOFF})
        db.commit()
        print(f"  -> Inserted {r1m.rowcount:,} 1M candles")

    print("[Phase 1] Complete.")


# ===========================================================================
# PHASE 2 — AngelOne fetch + resample for missing coverage
# ===========================================================================

def _tickers_missing_1w_coverage():
    """Active NSE tickers that have no 1W candle in the 2yr->5yr range."""
    with SessionLocal() as db:
        return db.execute(sa_text("""
            SELECT sm.ticker
            FROM stock_metadata sm
            WHERE sm.is_active = TRUE AND sm.exchange = 'NSE'
              AND NOT EXISTS (
                  SELECT 1 FROM candles c
                  WHERE c.ticker    = sm.ticker
                    AND c.timeframe = '1W'
                    AND c.timestamp::date >= :from_d
                    AND c.timestamp::date <  :to_d
              )
            ORDER BY sm.ticker
        """), {"from_d": FIVE_YR_CUTOFF, "to_d": TWO_YR_CUTOFF}).fetchall()


def _tickers_missing_1m_coverage():
    """Active NSE tickers that have no 1M candle older than 5yr cutoff."""
    with SessionLocal() as db:
        return db.execute(sa_text("""
            SELECT sm.ticker
            FROM stock_metadata sm
            WHERE sm.is_active = TRUE AND sm.exchange = 'NSE'
              AND NOT EXISTS (
                  SELECT 1 FROM candles c
                  WHERE c.ticker    = sm.ticker
                    AND c.timeframe = '1M'
                    AND c.timestamp::date < :cutoff
              )
            ORDER BY sm.ticker
        """), {"cutoff": FIVE_YR_CUTOFF}).fetchall()


def _date_chunks(start: date, end: date, chunk_days: int = 365):
    cur = start
    while cur < end:
        yield cur, min(cur + timedelta(days=chunk_days), end)
        cur = cur + timedelta(days=chunk_days)


def _insert_into_stock_data(ticker: str, daily_rows: list) -> int:
    """
    Store fetched ONE_DAY candles into stock_data (the staging table).
    AngelOne returns {timestamp (tz-aware datetime), open, high, low, close, volume}.
    adj_close is set to close (AngelOne doesn't provide it).
    """
    if not daily_rows:
        return 0
    inserted = 0
    with SessionLocal() as db:
        for i in range(0, len(daily_rows), 500):
            batch = daily_rows[i:i + 500]
            r = db.execute(sa_text("""
                INSERT INTO stock_data (ticker, date, open, high, low, close, adj_close, volume)
                VALUES (:ticker, :date, :open, :high, :low, :close, :close, :volume)
                ON CONFLICT ON CONSTRAINT uix_ticker_date DO NOTHING
            """), [
                {
                    'ticker':  ticker,
                    'date':    c['timestamp'].date() if hasattr(c['timestamp'], 'date') else
                               datetime.strptime(str(c['timestamp'])[:10], '%Y-%m-%d').date(),
                    'open':    float(c['open']),
                    'high':    float(c['high']),
                    'low':     float(c['low']),
                    'close':   float(c['close']),
                    'volume':  int(c.get('volume', 0) or 0),
                }
                for c in batch
            ])
            inserted += r.rowcount
        db.commit()
    return inserted


def _phase2_fetch_into_stock_data(from_d: date, to_d: date, missing_tickers: list,
                                   label: str) -> int:
    """
    Fetch ONE_DAY candles from AngelOne for each missing ticker in [from_d, to_d)
    and store them in stock_data. Returns total rows inserted.
    """
    from historical_service import historical_service

    if not historical_service.is_logged_in:
        print("[Phase 2] Logging in to AngelOne...")
        if not historical_service.login():
            print("[Phase 2] ERROR: AngelOne login failed.")
            return 0

    total          = len(missing_tickers)
    inserted_total = 0
    skipped        = 0

    print(f"\n[Phase 2] Fetching 1D for {label}: {total} tickers ({from_d} -> {to_d})")
    if total == 0:
        print(f"[Phase 2] Nothing to fetch for {label}.")
        return 0

    for i, (ticker,) in enumerate(missing_tickers, 1):
        try:
            daily_rows = []
            for chunk_start, chunk_end in _date_chunks(from_d, to_d):
                rows = historical_service.get_historical_candles(
                    ticker=ticker,
                    interval='ONE_DAY',
                    from_date=chunk_start,
                    to_date=chunk_end,
                )
                daily_rows.extend(rows or [])
                time.sleep(0.5)   # rate gate between chunks

            if daily_rows:
                n = _insert_into_stock_data(ticker, daily_rows)
                inserted_total += n
            else:
                skipped += 1

        except KeyboardInterrupt:
            print(f"\n[Phase 2] Interrupted at {ticker} ({i}/{total}). stock_data rows so far: {inserted_total:,}")
            return inserted_total
        except Exception as e:
            print(f"  [WARN] {ticker}: {e}")
            skipped += 1
            time.sleep(2)

        if i % 100 == 0 or i == total:
            print(f"  [{i}/{total}] {label} — {inserted_total:,} stock_data rows inserted, {skipped} skipped")

        time.sleep(1.2)   # rate gate between tickers

    print(f"[Phase 2] {label}: fetched {inserted_total:,} rows into stock_data, skipped {skipped} tickers.")
    return inserted_total


def phase2_fetch_missing_1w() -> None:
    """
    For tickers missing 1W coverage:
      1. Fetch 3yr of 1D from AngelOne -> store in stock_data
      2. Re-run Phase 1 SQL to aggregate stock_data -> candles(1W)
    """
    missing = _tickers_missing_1w_coverage()
    if not missing:
        print("[Phase 2] 1W: all tickers already have coverage — nothing to fetch.")
        return

    fetched = _phase2_fetch_into_stock_data(
        from_d=FIVE_YR_CUTOFF, to_d=TWO_YR_CUTOFF,
        missing_tickers=missing, label="1W (3yr range)"
    )

    if fetched > 0:
        print("[Phase 2] 1W: re-running SQL aggregation with newly fetched stock_data rows...")
        with SessionLocal() as db:
            r = db.execute(sa_text("""
                INSERT INTO candles
                    (ticker, timeframe, timestamp, open, high, low, close, volume,
                     is_completed, is_backfilled, data_source)
                SELECT
                    ticker, '1W',
                    DATE_TRUNC('week', date)::timestamp,
                    (ARRAY_AGG(open  ORDER BY date ASC))[1],
                    MAX(high), MIN(low),
                    (ARRAY_AGG(close ORDER BY date DESC))[1],
                    SUM(volume)::bigint,
                    TRUE, TRUE, 'SD_AGG'
                FROM stock_data
                WHERE date >= :from_d AND date < :to_d
                  AND open IS NOT NULL AND close IS NOT NULL AND open > 0
                GROUP BY ticker, DATE_TRUNC('week', date)
                ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
            """), {"from_d": FIVE_YR_CUTOFF, "to_d": TWO_YR_CUTOFF})
            db.commit()
            print(f"[Phase 2] 1W: inserted {r.rowcount:,} new candles(1W)")


def phase2_fetch_missing_1m() -> None:
    """
    For tickers missing 1M coverage:
      1. Fetch 5yr of 1D from AngelOne -> store in stock_data
      2. Re-run Phase 1 SQL to aggregate stock_data -> candles(1M)
    """
    missing = _tickers_missing_1m_coverage()
    if not missing:
        print("[Phase 2] 1M: all tickers already have coverage — nothing to fetch.")
        return

    fetched = _phase2_fetch_into_stock_data(
        from_d=TEN_YR_CUTOFF, to_d=FIVE_YR_CUTOFF,
        missing_tickers=missing, label="1M (5yr range)"
    )

    if fetched > 0:
        print("[Phase 2] 1M: re-running SQL aggregation with newly fetched stock_data rows...")
        with SessionLocal() as db:
            r = db.execute(sa_text("""
                INSERT INTO candles
                    (ticker, timeframe, timestamp, open, high, low, close, volume,
                     is_completed, is_backfilled, data_source)
                SELECT
                    ticker, '1M',
                    DATE_TRUNC('month', date)::timestamp,
                    (ARRAY_AGG(open  ORDER BY date ASC))[1],
                    MAX(high), MIN(low),
                    (ARRAY_AGG(close ORDER BY date DESC))[1],
                    SUM(volume)::bigint,
                    TRUE, TRUE, 'SD_AGG'
                FROM stock_data
                WHERE date < :cutoff
                  AND open IS NOT NULL AND close IS NOT NULL AND open > 0
                GROUP BY ticker, DATE_TRUNC('month', date)
                ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
            """), {"cutoff": FIVE_YR_CUTOFF})
            db.commit()
            print(f"[Phase 2] 1M: inserted {r.rowcount:,} new candles(1M)")


# ===========================================================================
# Helpers for startup integration (called from main.py)
# ===========================================================================

def needs_1w_backfill() -> bool:
    """True if more than 50 active NSE tickers are missing any 1W coverage."""
    with SessionLocal() as db:
        missing = db.execute(sa_text("""
            SELECT COUNT(*)
            FROM stock_metadata sm
            WHERE sm.is_active = TRUE AND sm.exchange = 'NSE'
              AND NOT EXISTS (
                  SELECT 1 FROM candles c
                  WHERE c.ticker = sm.ticker AND c.timeframe = '1W'
              )
        """)).scalar()
    return (missing or 0) > 50


def needs_1m_backfill() -> bool:
    """True if more than 20 active NSE tickers are missing any 1M coverage."""
    with SessionLocal() as db:
        missing = db.execute(sa_text("""
            SELECT COUNT(*)
            FROM stock_metadata sm
            WHERE sm.is_active = TRUE AND sm.exchange = 'NSE'
              AND NOT EXISTS (
                  SELECT 1 FROM candles c
                  WHERE c.ticker = sm.ticker AND c.timeframe = '1M'
              )
        """)).scalar()
    return (missing or 0) > 20


def run_startup_backfill() -> None:
    """
    Called from main.py startup task.
    Step 1 — Phase 1: SQL aggregate existing stock_data -> candles(1W/1M). Fast, idempotent.
    Step 2 — Phase 2: For tickers still missing coverage, fetch 1D from AngelOne ->
              store in stock_data -> re-aggregate. Skips automatically once complete.
    """
    print("[1W/1M Backfill] Starting startup backfill check...")
    phase1_migrate_stock_data()

    if needs_1w_backfill():
        print("[1W/1M Backfill] 1W still missing — fetching 3yr 1D from AngelOne...")
        phase2_fetch_missing_1w()

    if needs_1m_backfill():
        print("[1W/1M Backfill] 1M still missing — fetching 5yr 1D from AngelOne...")
        phase2_fetch_missing_1m()

    print("[1W/1M Backfill] Startup backfill complete.")


# ===========================================================================
# CLI entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Backfill candles 1W and 1M from stock_data + AngelOne'
    )
    parser.add_argument('--phase1',     action='store_true', help='SQL migration only (fast)')
    parser.add_argument('--phase2',     action='store_true', help='AngelOne fetch only (slow)')
    parser.add_argument('--timeframe',  choices=['1W', '1M'], default=None,
                        help='Limit phase 2 to one timeframe')
    args = parser.parse_args()

    run_both = not args.phase1 and not args.phase2

    if args.phase1 or run_both:
        phase1_migrate_stock_data()

    if args.phase2 or run_both:
        if args.timeframe == '1M':
            phase2_fetch_missing_1m()
        elif args.timeframe == '1W':
            phase2_fetch_missing_1w()
        else:
            phase2_fetch_missing_1w()
            phase2_fetch_missing_1m()

    print("\n[backfill_weekly_monthly] All done.")


if __name__ == '__main__':
    main()
