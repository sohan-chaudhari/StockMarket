"""
Backfill unified `candles` table from legacy `intraday_candles_*` tables.
=========================================================================
Batched, resumable, keyset migration with add-only semantics across ALL
relevant legacy timeframes:

  intraday_candles_5min  -> unified 5m
  intraday_candles_15min -> unified 15m
  intraday_candles_30min -> unified 30m
  intraday_candles_1h    -> unified 1h
  intraday_candles_1min  -> unified 1m

Explicitly NOT migrated:
  - 4h  : no legacy intraday_candles_4h source table exists (never invented)
  - 1D  : unified 1D is maintained by the daily pipeline; handled separately
  - *_backup_YYYYMMDD_* : redundant pre-cleanup snapshots (subsets of primary)

Design:
  - INSERT ... SELECT ... ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING
  - Never deletes from legacy tables (they remain completely untouched).
  - Keyset pagination on (ticker, timestamp) driven by the SOURCE table, so it
    is safe to re-run / resume after interruption without skipping or re-scanning.
    The cursor advances past the current source batch even when every row in the
    batch already exists in `candles` (no premature termination).
  - Batched so a crash mid-run only leaves the current batch uncommitted
    (each batch is its own transaction via engine.begin()).
  - Idempotent: re-running copies nothing that is already present.

Usage:
    python backfill_unified_from_legacy.py                       # all TFs, full range
    python backfill_unified_from_legacy.py --sources 5m 15m      # subset of TFs
    python backfill_unified_from_legacy.py --window-start 2026-05-18 --window-end 2026-07-01
        # optional date window applied to all selected sources

NOT RUN as part of the audit — safe to run later (approved step).
"""

import argparse
from datetime import datetime

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from database import engine
from sqlalchemy import text

BATCH_SIZE = 5000  # rows per keyset chunk / per transaction

# Source table -> unified timeframe (order matters only for reporting).
DEFAULT_SOURCES = [
    ("intraday_candles_5min", "5m"),
    ("intraday_candles_15min", "15m"),
    ("intraday_candles_30min", "30m"),
    ("intraday_candles_1h", "1h"),
    ("intraday_candles_1min", "1m"),
]


def _naive(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)


def migrate_table(conn, table: str, tf: str, win_start=None, win_end=None,
                  resume_ticker=None, resume_ts=None) -> int:
    """Migrate `table` → candles(timeframe=tf). Returns rows inserted (new).

    Each batch is committed by the caller (via conn.commit()) immediately after
    execution, so progress is durable per-batch and interruption only loses the
    in-flight batch (re-running resumes cleanly via the keyset cursor).
    """
    first = conn.execute(text(
        "SELECT ticker, timestamp FROM {table} ORDER BY ticker, timestamp LIMIT 1".format(table=table)
    )).fetchone()
    if first is None:
        print(f"[{tf}] {table}: empty, nothing to migrate")
        return 0

    total = 0
    cursor_ticker = resume_ticker
    cursor_ts = resume_ts
    if resume_ticker is not None:
        print(f"[resume] {table}->{tf}: starting from {resume_ticker}@{resume_ts}")

    while True:
        params = {"lim": BATCH_SIZE}
        where = []
        if cursor_ticker is not None:
            where.append("(src.ticker > :tick OR (src.ticker = :tick AND src.timestamp > :ts))")
            params["tick"] = cursor_ticker
            params["ts"] = cursor_ts
        if win_start is not None:
            where.append("src.timestamp >= :ws")
            params["ws"] = win_start
        if win_end is not None:
            where.append("src.timestamp < :we")
            params["we"] = win_end

        where_sql = " AND ".join(where) if where else "TRUE"

        # 1) Peek the next source batch (cursor advances even if all rows conflict).
        batch = conn.execute(text(f"""
            SELECT src.ticker, src.timestamp
            FROM {table} src
            WHERE {where_sql}
            ORDER BY src.ticker ASC, src.timestamp ASC
            LIMIT :lim
        """), params).fetchall()
        if not batch:
            break

        # 2) Insert this exact batch; ON CONFLICT DO NOTHING keeps it additive.
        batch_where = where_sql + f"""
            AND (src.ticker < :b_tick
                 OR (src.ticker = :b_tick AND src.timestamp <= :b_ts))
        """
        batch_params = dict(params)
        batch_params["b_tick"] = batch[-1][0]
        batch_params["b_ts"] = batch[-1][1]

        res = conn.execute(text(f"""
            INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume,
                                 is_completed, data_source, is_backfilled)
            SELECT src.ticker, '{tf}', src.timestamp, src.open, src.high, src.low, src.close,
                   COALESCE(src.volume, 0), TRUE, 'BACKFILL', TRUE
            FROM {table} src
            WHERE {batch_where}
            ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING
        """), batch_params)
        inserted = res.rowcount
        total += inserted
        conn.commit()

        # 3) Always advance past this source batch.
        cursor_ticker, cursor_ts = batch[-1][0], batch[-1][1]
        print(f"[{table}->{tf}] batch inserted={inserted} cumulative={total} "
              f"cursor={cursor_ticker}@{cursor_ts}")

    return total


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy intraday tables into unified candles")
    parser.add_argument("--sources", nargs="+", default=None,
                        help="Timeframes to migrate (default: all). Example: 5m 15m")
    parser.add_argument("--window-start", default=None, help="Optional ISO start (e.g. 2026-05-18)")
    parser.add_argument("--window-end", default=None, help="Optional ISO end (exclusive, e.g. 2026-07-01)")
    parser.add_argument("--resume-ticker", default=None,
                        help="Resume the FIRST listed source from this ticker (keyset cursor)")
    parser.add_argument("--resume-ts", default=None,
                        help="Resume the FIRST listed source from this timestamp (keyset cursor)")
    args = parser.parse_args()

    if args.sources:
        by_tf = {tf: table for table, tf in DEFAULT_SOURCES}
        sources = [(by_tf[s], s) for s in args.sources]
    else:
        sources = DEFAULT_SOURCES

    win_start = _naive(args.window_start) if args.window_start else None
    win_end = _naive(args.window_end) if args.window_end else None
    resume_ticker = args.resume_ticker
    resume_ts = _naive(args.resume_ts) if args.resume_ts else None

    grand_total = 0
    with engine.connect() as conn:
        for i, (table, tf) in enumerate(sources):
            print(f"[MIGRATE] {table} -> unified {tf}")
            rt = resume_ticker if i == 0 else None
            rts = resume_ts if i == 0 else None
            inserted = migrate_table(conn, table, tf, win_start, win_end, rt, rts)
            print(f"[SUMMARY] {table} -> candles[{tf}]: {inserted} new rows")
            grand_total += inserted
    print(f"[TOTAL] {grand_total:,} new rows inserted across {len(sources)} timeframe(s)")


if __name__ == "__main__":
    main()