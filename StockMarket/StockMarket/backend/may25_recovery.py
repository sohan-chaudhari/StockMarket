"""
May 25, 2026 — Emergency 5m Candle Recovery
============================================
Recovers genuinely missing 5-minute candles for the 2026-05-25 NSE session.

Rules:
  - Dry-run by default.  Pass --execute only after reviewing the dry-run.
  - Never overwrites an existing candle (ON CONFLICT … DO NOTHING).
  - Never writes to any deprecated table.
  - Treats the AngelOne provider response as the sole source of truth.
  - Skips tickers the provider doesn't recognise (permanent errors).
  - Blocks execution during NSE market hours (09:00–16:00 IST).
  - Writes live progress to may25_progress.json every 10 tickers.

Usage:
    python may25_recovery.py              # dry-run: report only, no writes
    python may25_recovery.py --execute    # write after approving the dry-run
    python may25_status.py                # check progress while running
"""

import sys
import os
import time
import json
import argparse
import logging
import pathlib
import threading
from datetime import date, datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Path setup ────────────────────────────────────────────────────────────────
backend_dir = pathlib.Path(__file__).parent.resolve()
sys.path.insert(0, str(backend_dir))

from dotenv import load_dotenv
load_dotenv(dotenv_path=backend_dir / ".env")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("may25_recovery")

# ── Constants ─────────────────────────────────────────────────────────────────
TARGET_DATE     = date(2026, 5, 25)
INTERVAL        = "FIVE_MINUTE"
EXCHANGE        = "NSE"
MAX_WORKERS     = 3
MAX_RETRIES     = 3
RETRY_DELAYS    = [5, 15, 30]   # seconds between retries; rate-limit errors need longer waits
MIN_REQUEST_GAP = 0.4           # enforced minimum gap between any two API calls (~2.5 req/sec)
PROGRESS_FILE   = backend_dir / "may25_progress.json"
PROGRESS_EVERY  = 10        # write progress file every N tickers

IST = timezone(timedelta(hours=5, minutes=30))

# ─────────────────────────────────────────────────────────────────────────────
# Global rate limiter — shared across all worker threads.
# Enforces MIN_REQUEST_GAP seconds between any two AngelOne API calls so we
# never exceed ~2–3 req/sec regardless of thread count.
# ─────────────────────────────────────────────────────────────────────────────
_rate_lock       = threading.Lock()
_last_api_call   = [0.0]   # mutable container so threads share one timestamp

def _rate_gate():
    """Block the calling thread until MIN_REQUEST_GAP has elapsed since the
    last API call, then update the timestamp atomically."""
    with _rate_lock:
        now     = time.monotonic()
        wait    = MIN_REQUEST_GAP - (now - _last_api_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_api_call[0] = time.monotonic()


# ─────────────────────────────────────────────────────────────────────────────
# Progress tracker (thread-safe)
# ─────────────────────────────────────────────────────────────────────────────

class Progress:
    def __init__(self, total: int, mode: str):
        self._lock = threading.Lock()
        self.total          = total
        self.done           = 0
        self.with_data      = 0
        self.no_data        = 0
        self.perm_errors    = 0
        self.retry_errors   = 0
        self.candles_found  = 0
        self.already_in_db  = 0
        self.to_insert      = 0
        self.mode           = mode          # "dry-run" or "execute"
        self.inserted       = 0             # execute mode only
        self.started_at     = datetime.now(tz=IST).isoformat()
        self.phase          = "fetching"    # fetching → computing → inserting → done
        self._write()

    def update(self, candles: int, already: int, missing: int,
               has_data: bool, perm_err: bool, retry_err: bool):
        with self._lock:
            self.done       += 1
            self.candles_found  += candles
            self.already_in_db  += already
            self.to_insert      += missing
            if perm_err:
                self.perm_errors += 1
            elif retry_err:
                self.retry_errors += 1
            elif has_data:
                self.with_data += 1
            else:
                self.no_data += 1
            if self.done % PROGRESS_EVERY == 0 or self.done == self.total:
                self._write()

    def set_phase(self, phase: str):
        with self._lock:
            self.phase = phase
            self._write()

    def add_inserted(self, n: int):
        with self._lock:
            self.inserted += n
            self._write()

    def _write(self):
        elapsed = (datetime.now(tz=IST) - datetime.fromisoformat(self.started_at)).total_seconds()
        rate = self.done / elapsed if elapsed > 0 else 0
        remaining_tickers = self.total - self.done
        eta_seconds = int(remaining_tickers / rate) if rate > 0 else None
        eta_str = None
        if eta_seconds is not None:
            h, rem = divmod(eta_seconds, 3600)
            m, s   = divmod(rem, 60)
            eta_str = f"{h:02d}:{m:02d}:{s:02d}"

        data = {
            "phase":            self.phase,
            "mode":             self.mode,
            "started_at":       self.started_at,
            "as_of":            datetime.now(tz=IST).isoformat(),
            "elapsed_sec":      round(elapsed, 1),
            "tickers_total":    self.total,
            "tickers_done":     self.done,
            "tickers_remaining":remaining_tickers,
            "pct_complete":     round(100 * self.done / self.total, 1) if self.total else 0,
            "tickers_per_sec":  round(rate, 2),
            "eta":              eta_str,
            "with_data":        self.with_data,
            "no_data":          self.no_data,
            "perm_errors":      self.perm_errors,
            "retry_errors":     self.retry_errors,
            "candles_from_provider": self.candles_found,
            "already_in_db":    self.already_in_db,
            "to_insert":        self.to_insert,
            "inserted":         self.inserted,
        }
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(PROGRESS_FILE)      # atomic replace


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def abort_if_market_hours():
    now = datetime.now(tz=IST)
    open_  = now.replace(hour=9,  minute=0, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if open_ <= now <= close_:
        log.error(
            "NSE market is currently open (%s IST). "
            "Running generateSession() would invalidate the live WebSocket feed. "
            "Re-run after 16:00 IST.", now.strftime("%H:%M")
        )
        sys.exit(1)


def get_engine():
    from sqlalchemy import create_engine
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:medikart%403145@localhost:5432/stock_data"
    )
    return create_engine(db_url, pool_size=5, max_overflow=5, pool_pre_ping=True)


def get_universe(engine) -> List[str]:
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT ticker FROM candles "
            "WHERE timeframe = '5m' AND is_backfilled = true "
            "ORDER BY ticker"
        )).fetchall()
    return [r[0] for r in rows]


def get_existing_may25(engine, tickers: List[str]) -> Dict[str, set]:
    from sqlalchemy import text
    chunk_size = 500
    result: Dict[str, set] = {}
    day_start = datetime(2026, 5, 25, 0, 0, 0)
    day_end   = datetime(2026, 5, 26, 0, 0, 0)
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        placeholders = ", ".join(f":t{j}" for j in range(len(chunk)))
        params: Dict = {f"t{j}": t for j, t in enumerate(chunk)}
        params["ds"] = day_start
        params["de"] = day_end
        sql = f"""
            SELECT ticker, timestamp FROM candles
            WHERE timeframe = '5m'
              AND timestamp >= :ds AND timestamp < :de
              AND ticker IN ({placeholders})
        """
        with engine.connect() as conn:
            for ticker, ts in conn.execute(text(sql), params).fetchall():
                naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
                result.setdefault(ticker, set()).add(naive)
    return result


def _norm(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo else ts


# ─────────────────────────────────────────────────────────────────────────────
# Provider fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_one(ticker: str, hist_svc) -> Tuple[str, List[dict], Optional[str]]:
    from historical_service import HistoricalFetchError
    for attempt in range(MAX_RETRIES):
        try:
            _rate_gate()   # enforce global inter-request gap before every attempt
            candles = hist_svc.get_historical_candles(
                ticker=ticker,
                interval=INTERVAL,
                from_date=TARGET_DATE,
                to_date=TARGET_DATE,
                exchange=EXCHANGE,
                raise_on_error=True,
            )
            return (ticker, candles, None)
        except HistoricalFetchError as exc:
            if not exc.retryable:
                return (ticker, [], f"PERMANENT: {exc}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
            else:
                return (ticker, [], f"RETRYABLE_EXHAUSTED: {exc}")
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
            else:
                return (ticker, [], f"EXCEPTION: {exc}")
    return (ticker, [], "UNKNOWN")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="May 25 5m candle recovery")
    parser.add_argument("--execute", action="store_true",
                        help="Perform actual inserts. Omit for dry-run (default).")
    args = parser.parse_args()
    dry_run = not args.execute

    abort_if_market_hours()

    mode = "dry-run" if dry_run else "execute"
    if not dry_run:
        log.warning("=" * 60)
        log.warning("EXECUTE MODE — rows will be inserted into the database.")
        log.warning("=" * 60)

    # ── DB + universe ─────────────────────────────────────────────────────────
    log.info("Connecting to database …")
    engine = get_engine()

    log.info("Loading universe …")
    universe = get_universe(engine)
    log.info("Universe: %d tickers", len(universe))

    log.info("Loading existing May 25 candles from DB …")
    existing = get_existing_may25(engine, universe)

    before_complete = sum(1 for t in universe if len(existing.get(t, set())) >= 75)
    before_partial  = sum(1 for t in universe if 0 < len(existing.get(t, set())) < 75)
    before_absent   = sum(1 for t in universe if len(existing.get(t, set())) == 0)
    log.info("DB May 25 state: %d complete, %d partial, %d absent",
             before_complete, before_partial, before_absent)

    # Skip tickers that are already complete — no AngelOne call needed.
    fetch_universe = [t for t in universe if len(existing.get(t, set())) < 75]
    log.info("Fetch universe after skipping complete tickers: %d (skipped %d already-complete)",
             len(fetch_universe), len(universe) - len(fetch_universe))

    # ── AngelOne login ────────────────────────────────────────────────────────
    log.info("Logging in to AngelOne …")
    log.warning("generateSession() will refresh the JWT — live WS will auto-reconnect.")

    from angelone_service import angelone_service as ao_svc
    from historical_service import historical_service as hist_svc

    log.info("Loading instruments cache …")
    ao_svc.load_instruments()

    if not hist_svc.login():
        log.error("AngelOne login failed. Aborting.")
        sys.exit(1)

    # ── Fetch from provider ───────────────────────────────────────────────────
    prog = Progress(len(fetch_universe), mode)
    log.info("Fetching from AngelOne (%d tickers, %d workers) …",
             len(fetch_universe), MAX_WORKERS)

    results: Dict[str, Tuple[List[dict], Optional[str]]] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_one, t, hist_svc): t for t in fetch_universe}
        for fut in as_completed(futures):
            ticker, candles, error = fut.result()
            results[ticker] = (candles, error)

            existing_ts  = existing.get(ticker, set())
            perm_err     = bool(error and "PERMANENT" in error)
            retry_err    = bool(error and not perm_err)
            has_data     = not error and bool(candles)

            if has_data:
                provider_ts  = {_norm(c["timestamp"]) for c in candles}
                already      = len(provider_ts & existing_ts)
                missing      = len(provider_ts - existing_ts)
            else:
                already = missing = 0

            prog.update(
                candles  = len(candles) if candles else 0,
                already  = already,
                missing  = missing,
                has_data = has_data,
                perm_err = perm_err,
                retry_err= retry_err,
            )

    prog.set_phase("computing")

    # ── Build insert plan ─────────────────────────────────────────────────────
    insert_plan: Dict[str, List[dict]] = {}
    permanent_errors: List[Tuple[str, str]] = []
    retryable_errors: List[Tuple[str, str]] = []

    total_provider_candles = 0
    total_already_present  = 0
    total_to_insert        = 0
    tickers_with_data      = 0
    tickers_no_data        = 0
    tickers_perm_err       = 0
    tickers_retry_err      = 0

    for ticker in fetch_universe:
        candles, error = results.get(ticker, ([], "NOT_FETCHED"))
        existing_ts = existing.get(ticker, set())

        if error:
            if "PERMANENT" in error:
                tickers_perm_err += 1
                permanent_errors.append((ticker, error))
            else:
                tickers_retry_err += 1
                retryable_errors.append((ticker, error))
            continue

        if not candles:
            tickers_no_data += 1
            continue

        tickers_with_data += 1
        total_provider_candles += len(candles)

        new_rows = []
        for c in candles:
            ts = _norm(c["timestamp"])
            if ts in existing_ts:
                total_already_present += 1
            else:
                total_to_insert += 1
                new_rows.append({
                    "ticker":    ticker,
                    "timestamp": ts,
                    "open":      c["open"],
                    "high":      c["high"],
                    "low":       c["low"],
                    "close":     c["close"],
                    "volume":    c["volume"],
                })

        if new_rows:
            insert_plan[ticker] = new_rows

    tickers_unavailable = tickers_perm_err + tickers_retry_err + tickers_no_data

    # ── Print dry-run report ──────────────────────────────────────────────────
    sep = "-" * 65
    print()
    print("=" * 65)
    print("  MAY 25 RECOVERY - DRY-RUN REPORT" if dry_run else
          "  MAY 25 RECOVERY - PRE-EXECUTE SUMMARY")
    print("=" * 65)
    print(f"  {'Metric':<47} {'Value':>8}")
    print(sep)
    print(f"  {'1.  Eligible tickers':<47} {len(universe):>8,}")
    print(f"  {'2.  Tickers with AngelOne data':<47} {tickers_with_data:>8,}")
    print(f"  {'3.  Tickers without data (empty response)':<47} {tickers_no_data:>8,}")
    print(f"  {'    Tickers: permanent error (not found)':<47} {tickers_perm_err:>8,}")
    print(f"  {'    Tickers: retryable error (exhausted)':<47} {tickers_retry_err:>8,}")
    print(f"  {'    Tickers unavailable total':<47} {tickers_unavailable:>8,}")
    print(sep)
    print(f"  {'4.  Expected candles (tickers_with_data × 75)':<47} {tickers_with_data * 75:>8,}")
    print(f"  {'5.  Candles actually returned by AngelOne':<47} {total_provider_candles:>8,}")
    print(f"  {'6.  Candles already present in DB (skipped)':<47} {total_already_present:>8,}")
    print(f"  {'7.  Candles genuinely missing from DB':<47} {total_to_insert:>8,}")
    print(f"  {'8.  Exact rows that WOULD be inserted':<47} {total_to_insert:>8,}")
    print(sep)
    print(f"  {'9.  API requests made':<47} {len(universe):>8,}")
    print(f"  {'10. Permanent errors':<47} {tickers_perm_err:>8,}")
    print(f"  {'    Retryable errors (exhausted)':<47} {tickers_retry_err:>8,}")
    print(sep)
    print(f"  {'DB state BEFORE — complete sessions (75 rows)':<47} {before_complete:>8,}")
    print(f"  {'DB state BEFORE — partial sessions (1-74 rows)':<47} {before_partial:>8,}")
    print(f"  {'DB state BEFORE — absent sessions (0 rows)':<47} {before_absent:>8,}")
    print("=" * 65)

    # Anomaly detection
    anomalies = []
    for ticker in universe:
        candles, error = results.get(ticker, ([], None))
        if candles and len(candles) > 75:
            anomalies.append(f"  {ticker}: {len(candles)} candles (> 75 expected)")
        if candles:
            for c in candles:
                ts = _norm(c["timestamp"])
                if ts.weekday() >= 5:
                    anomalies.append(f"  {ticker}: weekend timestamp {ts}")
                if not (9*60+15 <= ts.hour*60+ts.minute <= 15*60+30):
                    anomalies.append(f"  {ticker}: out-of-session timestamp {ts}")

    print()
    if anomalies:
        print(f"  11. ANOMALIES DETECTED ({len(anomalies)}):")
        for a in anomalies[:30]:
            print(f"  {a}")
        if len(anomalies) > 30:
            print(f"  … and {len(anomalies) - 30} more")
    else:
        print("  11. Anomalies: none detected")

    if permanent_errors:
        print(f"\n  PERMANENT ERRORS ({len(permanent_errors)} tickers):")
        for t, e in permanent_errors[:20]:
            print(f"    {t:<20}  {e}")
        if len(permanent_errors) > 20:
            print(f"    … and {len(permanent_errors) - 20} more")

    if retryable_errors:
        print(f"\n  RETRYABLE ERRORS ({len(retryable_errors)} tickers):")
        for t, e in retryable_errors[:10]:
            print(f"    {t:<20}  {e}")
        if len(retryable_errors) > 10:
            print(f"    … and {len(retryable_errors) - 10} more")

    if dry_run:
        prog.set_phase("done")
        print()
        print("  DRY-RUN COMPLETE — no rows written.")
        print(f"  Approve and run with --execute to insert {total_to_insert:,} rows.")
        print()
        return

    # ── Execute mode: actual inserts ──────────────────────────────────────────
    from sqlalchemy import text

    with engine.connect() as conn:
        before_count = conn.execute(text(
            "SELECT COUNT(*) FROM candles WHERE timeframe='5m' "
            "AND timestamp >= '2026-05-25' AND timestamp < '2026-05-26'"
        )).scalar()

    insert_sql = text("""
        INSERT INTO candles
            (ticker, timeframe, timestamp, open, high, low, close, volume,
             is_completed, data_source, is_backfilled)
        VALUES
            (:ticker, '5m', :timestamp, :open, :high, :low, :close, :volume,
             true, 'HISTORICAL', true)
        ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
    """)

    prog.set_phase("inserting")
    log.info("Inserting %d rows across %d tickers …", total_to_insert, len(insert_plan))
    total_inserted   = 0
    total_conflicted = 0

    for ticker, rows in insert_plan.items():
        with engine.begin() as conn:
            for row in rows:
                r = conn.execute(insert_sql, row)
                if r.rowcount == 1:
                    total_inserted   += 1
                else:
                    total_conflicted += 1
        prog.add_inserted(len(rows))

    with engine.connect() as conn:
        after_count = conn.execute(text(
            "SELECT COUNT(*) FROM candles WHERE timeframe='5m' "
            "AND timestamp >= '2026-05-25' AND timestamp < '2026-05-26'"
        )).scalar()

    # ── Post-insert verification ──────────────────────────────────────────────
    log.info("Running post-insert verification …")
    with engine.connect() as conn:
        dup_count = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT ticker, timestamp, COUNT(*) AS n
                FROM candles
                WHERE timeframe = '5m'
                  AND timestamp >= '2026-05-25' AND timestamp < '2026-05-26'
                GROUP BY ticker, timestamp HAVING COUNT(*) > 1
            ) t
        """)).scalar()

        ohlc_bad = conn.execute(text("""
            SELECT COUNT(*) FROM candles
            WHERE timeframe='5m' AND data_source='HISTORICAL'
              AND timestamp >= '2026-05-25' AND timestamp < '2026-05-26'
              AND (low > high OR open < low OR open > high
                   OR close < low OR close > high)
        """)).scalar()

        ts_bad = conn.execute(text("""
            SELECT COUNT(*) FROM candles
            WHERE timeframe='5m' AND data_source='HISTORICAL'
              AND timestamp >= '2026-05-25' AND timestamp < '2026-05-26'
              AND (EXTRACT(dow FROM timestamp) IN (0,6)
                OR EXTRACT(HOUR FROM timestamp)*60+EXTRACT(MINUTE FROM timestamp)
                   NOT BETWEEN 9*60+15 AND 15*60+30)
        """)).scalar()

        dist = conn.execute(text("""
            SELECT row_count, COUNT(*) FROM (
                SELECT ticker, COUNT(*) AS row_count FROM candles
                WHERE timeframe='5m'
                  AND timestamp >= '2026-05-25' AND timestamp < '2026-05-26'
                GROUP BY ticker
            ) t GROUP BY row_count ORDER BY row_count
        """)).fetchall()

    complete_after = sum(cnt for rows, cnt in dist if rows == 75)
    partial_after  = sum(cnt for rows, cnt in dist if rows < 75)

    print()
    print("=" * 65)
    print("  MAY 25 RECOVERY — EXECUTION COMPLETE")
    print("=" * 65)
    print(f"  {'Metric':<47} {'Value':>8}")
    print(sep)
    print(f"  {'Rows in DB before':<47} {before_count:>8,}")
    print(f"  {'Rows in DB after':<47} {after_count:>8,}")
    print(f"  {'Net new rows':<47} {after_count - before_count:>8,}")
    print(f"  {'Reported inserted':<47} {total_inserted:>8,}")
    print(f"  {'ON CONFLICT skips (execute loop)':<47} {total_conflicted:>8,}")
    print(sep)
    print(f"  {'VERIFY: duplicate (ticker,ts) pairs':<47} {dup_count:>8,}")
    print(f"  {'VERIFY: OHLC violations':<47} {ohlc_bad:>8,}")
    print(f"  {'VERIFY: out-of-session timestamps':<47} {ts_bad:>8,}")
    print(sep)
    print(f"  {'Sessions complete (75 rows) AFTER':<47} {complete_after:>8,}")
    print(f"  {'Sessions partial (<75 rows) AFTER':<47} {partial_after:>8,}")
    print("=" * 65)

    prog.set_phase("done")

    if dup_count > 0 or ohlc_bad > 0 or ts_bad > 0:
        log.error("VERIFICATION FAILED — dup=%d ohlc=%d ts=%d",
                  dup_count, ohlc_bad, ts_bad)
        sys.exit(2)

    log.info("All verification checks passed. May 25 recovery complete.")


if __name__ == "__main__":
    main()
