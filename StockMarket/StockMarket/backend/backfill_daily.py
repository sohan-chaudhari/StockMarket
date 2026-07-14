"""
One-time daily candle backfill for ALL tickers in stock_metadata (~5,500).

Strategy: Batch multi-ticker downloads (rate-limit friendly), with sub-batch
fallback for tickers missing from the batch result.

Usage:
    python backfill_daily.py                         # full run (resumes from checkpoint)
    python backfill_daily.py --force                  # re-fetch even if daily exists
    python backfill_daily.py --reset                  # clear checkpoint & start fresh
    python backfill_daily.py --status                 # show progress only
    python backfill_daily.py --retry-failed            # retry previously failed tickers
"""

import sys, os, time, json
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import yfinance as yf
import pandas as pd
from sqlalchemy import text as sa_text
from database import SessionLocal

CHECKPOINT_PATH = os.path.join(_backend_dir, ".backfill_checkpoint.json")
FAILED_PATH = os.path.join(_backend_dir, ".backfill_failed.json")
BATCH_SIZE = 40
BATCH_DELAY = 2.0
YF_PERIOD = "1y"
YF_INTERVAL = "1d"

YFINANCE_INDEX_MAP = {
    "NIFTY": "^NSEI", "SENSEX": "^BSESN", "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "^NIFTY_FIN_SERVICE.NS", "MIDCAP": "^NSEMDCP50",
    "SMALLCAP": "^SMALLCAP.NS",
}


def _yticker(clean: str) -> str:
    return YFINANCE_INDEX_MAP.get(clean, f"{clean}.NS")


def load_set(path: str) -> set:
    if not os.path.exists(path):
        return set()
    try:
        with open(path) as f:
            return set(json.load(f).get("completed", []))
    except Exception:
        return set()


def save_set(data: set, path: str):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"completed": sorted(data), "total": len(data)}, f)
    os.replace(tmp, path)


def clear_checkpoint():
    for p in [CHECKPOINT_PATH, FAILED_PATH]:
        if os.path.exists(p):
            os.remove(p)


def get_existing_daily_set(db) -> set:
    rows = db.execute(sa_text("SELECT DISTINCT ticker FROM candles WHERE timeframe='1D'")).fetchall()
    return {r[0] for r in rows}


def _df_to_rows(df, ticker: str) -> list:
    rows = []
    for idx, row in df.iterrows():
        try:
            ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if hasattr(idx, 'tz') and idx.tz is not None:
                ts = ts.astimezone(IST).replace(tzinfo=None)
            else:
                ts = ts.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)
            o = float(row.get('Open', 0) or 0)
            h = float(row.get('High', 0) or 0)
            l = float(row.get('Low', 0) or 0)
            c = float(row.get('Close', 0) or 0)
            v = int(row.get('Volume', 0) or 0)
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                continue
            h = max(o, h, c)
            l = min(o, l, c)
            rows.append({
                "ticker": ticker, "tf": "1D", "ts": ts,
                "open": o, "high": h, "low": l, "close": c, "vol": v,
            })
        except Exception:
            continue
    return rows


def _insert_rows(db, rows: list):
    stmt = sa_text("""
        INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
        VALUES (:ticker, :tf, :ts, :open, :high, :low, :close, :vol, true, 'BACKFILL', true)
        ON CONFLICT (ticker, timeframe, timestamp) DO UPDATE SET
            high = GREATEST(candles.high, EXCLUDED.high),
            low = LEAST(candles.low, EXCLUDED.low),
            open = EXCLUDED.open,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            data_source = EXCLUDED.data_source,
            is_backfilled = EXCLUDED.is_backfilled
    """)
    db.execute(stmt, rows)
    db.commit()


def _parse_single_df(df, ticker) -> list:
    if df is None or df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return _df_to_rows(df, ticker)


def _download_batch(ticker_list: list, timeout=25) -> dict:
    """Download a batch of tickers. Returns {ticker: [rows], ...}.
    Only tickers that appear in the result are included."""
    if not ticker_list:
        return {}
    yf_syms = " ".join(sorted(set(_yticker(t) for t in ticker_list)))
    try:
        df = yf.download(yf_syms, period=YF_PERIOD, interval=YF_INTERVAL,
                         progress=False, auto_adjust=True, timeout=timeout)
        if df is None or df.empty:
            return {}
    except Exception:
        return {}

    result = {}
    if isinstance(df.columns, pd.MultiIndex):
        tickers_in_result = set(df.columns.get_level_values(1))
        for t in ticker_list:
            ysym = _yticker(t)
            if ysym in tickers_in_result:
                sub = df.xs(ysym, axis=1, level=1)
                rows = _df_to_rows(sub, t)
                if rows:
                    result[t] = rows
    else:
        # Single ticker in batch
        rows = _parse_single_df(df, ticker_list[0])
        if rows:
            result[ticker_list[0]] = rows
    return result


def _download_individual(ticker: str, timeout=12) -> list:
    try:
        df = yf.download(_yticker(ticker), period=YF_PERIOD, interval=YF_INTERVAL,
                         progress=False, auto_adjust=True, timeout=timeout)
        return _parse_single_df(df, ticker)
    except Exception:
        return []


def backfill_batch(batch_tickers: list, existing: set) -> tuple:
    """Backfill one batch. Returns (ok_set, fail_set)."""
    pending = [t for t in batch_tickers if t not in existing]
    if not pending:
        return set(batch_tickers), set()

    ok_set = set()
    fail_set = set()

    # Try full batch
    result = _download_batch(pending)
    for t, rows in result.items():
        if rows:
            db = SessionLocal()
            try:
                _insert_rows(db, rows)
            finally:
                db.close()
            ok_set.add(t)

    missing = [t for t in pending if t not in result]
    if missing:
        # Try in sub-batches of 10
        for i in range(0, len(missing), 10):
            sub = missing[i:i+10]
            sub_result = _download_batch(sub, timeout=18)
            for t, rows in sub_result.items():
                if rows:
                    db = SessionLocal()
                    try:
                        _insert_rows(db, rows)
                    finally:
                        db.close()
                    ok_set.add(t)
            still_missing = [t for t in sub if t not in sub_result]
            # Individual fallback for the rest
            for t in still_missing:
                rows = _download_individual(t)
                if rows:
                    db = SessionLocal()
                    try:
                        _insert_rows(db, rows)
                    finally:
                        db.close()
                    ok_set.add(t)
                else:
                    fail_set.add(t)

    # Mark tickers that already had data as OK
    ok_set.update(t for t in batch_tickers if t not in pending)
    return ok_set, fail_set


def main():
    args = set(sys.argv[1:])
    force = "--force" in args
    reset = "--reset" in args
    status_only = "--status" in args
    retry_failed = "--retry-failed" in args

    if reset:
        clear_checkpoint()

    completed = load_set(CHECKPOINT_PATH)
    failed = load_set(FAILED_PATH)

    if retry_failed:
        completed -= failed
        failed = set()
        save_set(failed, FAILED_PATH)

    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text("SELECT DISTINCT ticker FROM stock_metadata ORDER BY ticker")
        ).fetchall()
        all_tickers = [r[0] for r in rows]
        existing_daily = get_existing_daily_set(db) if not force else set()
    finally:
        db.close()

    total = len(all_tickers)
    pending = [t for t in all_tickers if t not in completed]

    print(f"Total tickers in metadata: {total}")
    print(f"Already in checkpoint: {len(completed)}")
    print(f"Already have daily candles in DB: {len(existing_daily)}")
    print(f"Failed (skip unless --retry-failed): {len(failed)}")
    print(f"Pending (not in checkpoint): {len(pending)}")

    if status_only or not pending:
        return

    start_time = time.time()
    all_failed_this_run = []

    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        batch_start = time.time()
        ok_set, fail_set = backfill_batch(batch, existing_daily)

        completed.update(ok_set)
        all_failed_this_run.extend(fail_set)

        save_set(completed, CHECKPOINT_PATH)
        save_set(set(all_failed_this_run), FAILED_PATH)

        done_so_far = min(len(completed), len(pending))
        elapsed = time.time() - start_time
        rate = done_so_far / max(elapsed, 1)
        remaining = (len(pending) - done_so_far) / max(rate, 0.001)
        batch_time = time.time() - batch_start

        print(f"  batch {i//BATCH_SIZE+1}/{(len(pending)-1)//BATCH_SIZE+1}: "
              f"{done_so_far}/{len(pending)} ({done_so_far/max(len(pending),1)*100:.0f}%) "
              f"{rate:.1f}/s ETA {remaining:.0f}s "
              f"ok={len(ok_set & set(batch))} fail={len(fail_set & set(batch))} "
              f"batch={batch_time:.1f}s")

        if batch_time < BATCH_DELAY:
            time.sleep(BATCH_DELAY - batch_time)

    total_elapsed = time.time() - start_time
    total_with_data = len(get_existing_daily_set(SessionLocal()))
    print(f"\n{'='*50}")
    print(f"Done in {total_elapsed:.0f}s")
    print(f"Tickers now with daily candles: {total_with_data} / {total}")
    print(f"Succeeded this run: {len(pending) - len(all_failed_this_run)}")
    print(f"Failed: {len(all_failed_this_run)}")
    if all_failed_this_run:
        print(f"Samples: {', '.join(sorted(all_failed_this_run)[:20])}")


if __name__ == "__main__":
    main()