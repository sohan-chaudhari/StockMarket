"""
Bulk Recovery -- 5-minute candle gap-fill for all NSE sessions from 2026-05-26 to 2026-08-17.

DB-first strategy:
  1. Query DB for every (ticker, session_date) pair already complete (>=75 candles).
  2. Build fetch plan: per ticker, include only the 60-day chunks that still have gaps.
     Tickers with ALL sessions complete are skipped entirely.
  3. Fetch from AngelOne, insert with ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING.

Safety invariants:
  - Never overwrites an existing valid candle (DO NOTHING).
  - Never manufactures candles -- only inserts what AngelOne returns.
  - Aborts if run during market hours (09:00-16:00 IST).

Usage:
    python bulk_recovery.py          # dry-run (shows plan, zero writes)
    python bulk_recovery.py --execute
    python bulk_recovery.py --resume --execute  # skip already-completed chunks
"""

import sys, os, time, json, pathlib, threading, argparse, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Set, Tuple, Optional

backend_dir = pathlib.Path(__file__).parent.resolve()
sys.path.insert(0, str(backend_dir))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(dotenv_path=backend_dir / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bulk_recovery")

# ── Constants ─────────────────────────────────────────────────────────────────
RECOVERY_START   = date(2026, 5, 26)   # May 25 already recovered separately
RECOVERY_END     = date(2026, 8, 17)   # inclusive
CANDLES_COMPLETE = 75                   # full NSE session = 75 five-minute slots
CHUNK_DAYS       = 60                   # AngelOne historical API limit per request
MAX_WORKERS      = 1                    # 1 worker for resume (rate-limit recovery)
MIN_REQUEST_GAP  = 1.5                  # 1.5s gap for resume (~0.67 req/sec)
MAX_RETRIES      = 3
RETRY_DELAYS     = [30, 60, 120]        # longer delays for resume run
INTERVAL         = "FIVE_MINUTE"
EXCHANGE         = "NSE"
TIMEFRAME        = "5m"
IST              = timezone(timedelta(hours=5, minutes=30))
PROGRESS_FILE    = backend_dir / "bulk_recovery_progress.json"

# ── Rate gate ─────────────────────────────────────────────────────────────────
_rate_lock     = threading.Lock()
_last_api_call = [0.0]

def _rate_gate():
    with _rate_lock:
        now  = time.monotonic()
        wait = MIN_REQUEST_GAP - (now - _last_api_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_api_call[0] = time.monotonic()

# ── Misc helpers ──────────────────────────────────────────────────────────────
def get_engine():
    from sqlalchemy import create_engine
    url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:medikart%403145@localhost:5432/stock_data"
    )
    return create_engine(url, pool_size=8, max_overflow=4, pool_pre_ping=True)

def abort_if_market_hours():
    now = datetime.now(tz=IST)
    if (now.hour == 9) or (10 <= now.hour <= 15):
        log.error("Market hours (09:00-16:00 IST). Aborting to protect live feed.")
        sys.exit(1)

def make_chunks() -> List[Tuple[date, date]]:
    """Split recovery range into <=CHUNK_DAYS-day windows."""
    chunks: List[Tuple[date, date]] = []
    cs = RECOVERY_START
    while cs <= RECOVERY_END:
        ce = min(cs + timedelta(days=CHUNK_DAYS - 1), RECOVERY_END)
        chunks.append((cs, ce))
        cs = ce + timedelta(days=1)
    return chunks

def _norm(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo else ts

# ── DB scan phase ─────────────────────────────────────────────────────────────
def get_universe(engine) -> List[str]:
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT ticker FROM candles
            WHERE timeframe = :tf AND is_backfilled = true
            ORDER BY ticker
        """), {"tf": TIMEFRAME}).fetchall()
    return [r[0] for r in rows]

def get_trading_sessions(engine) -> List[date]:
    """
    Actual NSE trading session dates present in the DB.
    Using DB-observed dates as the session list avoids hardcoding holidays:
    any weekday where ZERO tickers have data simply won't appear here.
    """
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT DATE(timestamp) as d
            FROM candles
            WHERE timeframe = :tf
              AND timestamp >= :start
              AND timestamp  < :end_
            ORDER BY d
        """), {
            "tf":    TIMEFRAME,
            "start": datetime.combine(RECOVERY_START, datetime.min.time()),
            "end_":  datetime.combine(RECOVERY_END + timedelta(days=1), datetime.min.time()),
        }).fetchall()
    return [r[0] for r in rows]

def get_complete_pairs(engine) -> Dict[str, Set[date]]:
    """
    Returns ticker -> set of session dates already complete (>=CANDLES_COMPLETE rows).
    These (ticker, date) pairs need no AngelOne call.
    """
    from sqlalchemy import text
    log.info("Scanning DB for complete (ticker, session) pairs ...")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ticker, DATE(timestamp) as d
            FROM candles
            WHERE timeframe = :tf
              AND timestamp >= :start
              AND timestamp  < :end_
            GROUP BY ticker, DATE(timestamp)
            HAVING COUNT(*) >= :complete
        """), {
            "tf":       TIMEFRAME,
            "start":    datetime.combine(RECOVERY_START, datetime.min.time()),
            "end_":     datetime.combine(RECOVERY_END + timedelta(days=1), datetime.min.time()),
            "complete": CANDLES_COMPLETE,
        }).fetchall()

    result: Dict[str, Set[date]] = {}
    for ticker, d in rows:
        result.setdefault(ticker, set()).add(d)
    total = sum(len(v) for v in result.values())
    log.info("  %d complete pairs across %d tickers (no AngelOne call needed for these)",
             total, len(result))
    return result

# ── Fetch plan ────────────────────────────────────────────────────────────────
def build_fetch_plan(
    universe: List[str],
    complete_by_ticker: Dict[str, Set[date]],
    sessions: List[date],
    chunks: List[Tuple[date, date]],
) -> List[Tuple[str, date, date]]:
    """
    Returns (ticker, chunk_start, chunk_end) only for chunks that contain at
    least one session where this ticker's candle count is < CANDLES_COMPLETE.
    """
    chunk_sessions = [
        [d for d in sessions if cs <= d <= ce]
        for cs, ce in chunks
    ]

    plan: List[Tuple[str, date, date]] = []
    fully_skipped = 0

    for ticker in universe:
        tc = complete_by_ticker.get(ticker, set())
        ticker_added = False

        for (cs, ce), sess_in_chunk in zip(chunks, chunk_sessions):
            if not sess_in_chunk:
                continue
            if any(d not in tc for d in sess_in_chunk):
                plan.append((ticker, cs, ce))
                ticker_added = True

        if not ticker_added:
            fully_skipped += 1

    log.info(
        "Fetch plan: %d API calls for %d tickers (%d fully-complete tickers skipped)",
        len(plan), len(universe) - fully_skipped, fully_skipped,
    )
    return plan

# ── AngelOne fetch ────────────────────────────────────────────────────────────
def fetch_chunk(
    ticker: str,
    chunk_start: date,
    chunk_end: date,
    hist_svc,
) -> Tuple[str, date, date, List[dict], Optional[str]]:
    from historical_service import HistoricalFetchError

    for attempt in range(MAX_RETRIES):
        try:
            _rate_gate()
            candles = hist_svc.get_historical_candles(
                ticker=ticker,
                interval=INTERVAL,
                from_date=chunk_start,
                to_date=chunk_end,
                exchange=EXCHANGE,
                raise_on_error=True,
            )
            return (ticker, chunk_start, chunk_end, candles, None)
        except HistoricalFetchError as exc:
            if not exc.retryable:
                return (ticker, chunk_start, chunk_end, [], f"PERMANENT: {exc}")
            if attempt < MAX_RETRIES - 1:
                log.warning("Rate limit %s %s->%s attempt %d -- waiting %ds",
                            ticker, chunk_start, chunk_end, attempt + 1,
                            RETRY_DELAYS[attempt])
                time.sleep(RETRY_DELAYS[attempt])
            else:
                return (ticker, chunk_start, chunk_end, [], f"RETRYABLE_EXHAUSTED: {exc}")
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
            else:
                return (ticker, chunk_start, chunk_end, [], f"EXCEPTION: {exc}")

    return (ticker, chunk_start, chunk_end, [], "UNKNOWN")

# ── Progress tracking ─────────────────────────────────────────────────────────
class Progress:
    def __init__(self, total_chunks: int, mode: str):
        self.total                = total_chunks
        self.mode                 = mode
        self.done                 = 0
        self.candles_from_provider = 0
        self.rows_inserted        = 0
        self.permanent_errors     = 0
        self.retry_exhausted      = 0
        self.start                = time.monotonic()
        self._lock                = threading.Lock()
        self.completed_chunks: Set[str] = set()

    @staticmethod
    def _key(ticker: str, cs: date, ce: date) -> str:
        return f"{ticker}|{cs}|{ce}"

    def is_done(self, ticker: str, cs: date, ce: date) -> bool:
        return self._key(ticker, cs, ce) in self.completed_chunks

    def tick(self, ticker: str, cs: date, ce: date,
             n_provider: int, n_inserted: int, error: Optional[str]):
        with self._lock:
            self.done += 1
            self.candles_from_provider += n_provider
            self.rows_inserted         += n_inserted
            if error:
                if "PERMANENT" in error:
                    self.permanent_errors += 1
                elif "RETRYABLE" in error:
                    self.retry_exhausted  += 1
            else:
                self.completed_chunks.add(self._key(ticker, cs, ce))
            self._save()

    def _save(self):
        elapsed = time.monotonic() - self.start
        speed   = self.done / elapsed if elapsed > 0 else 0
        eta_str = None
        if speed > 0 and self.done < self.total:
            eta_sec = (self.total - self.done) / speed
            eta_dt  = datetime.now(tz=IST) + timedelta(seconds=eta_sec)
            eta_str = eta_dt.strftime("%Y-%m-%d %H:%M IST")
        pct = round(self.done / self.total * 100, 1) if self.total else 0
        d = {
            "mode":                 self.mode,
            "total_chunks":         self.total,
            "chunks_done":          self.done,
            "pct_complete":         pct,
            "candles_from_provider": self.candles_from_provider,
            "rows_inserted":        self.rows_inserted,
            "permanent_errors":     self.permanent_errors,
            "retry_exhausted":      self.retry_exhausted,
            "elapsed_sec":          round(elapsed),
            "eta":                  eta_str,
            "as_of":                datetime.now(tz=IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            "completed_chunks":     list(self.completed_chunks),
        }
        try:
            PROGRESS_FILE.write_text(json.dumps(d, indent=2))
        except Exception:
            pass

    def load_completed(self):
        if not PROGRESS_FILE.exists():
            return
        try:
            d = json.loads(PROGRESS_FILE.read_text())
            self.completed_chunks = set(d.get("completed_chunks", []))
            log.info("Resume: %d chunks already completed in previous run",
                     len(self.completed_chunks))
        except Exception:
            pass

# ── Insert ────────────────────────────────────────────────────────────────────
_INSERT_SQL = """
    INSERT INTO candles
        (ticker, timeframe, timestamp, open, high, low, close, volume,
         is_completed, data_source, is_backfilled)
    VALUES
        (:ticker, '5m', :timestamp, :open, :high, :low, :close, :volume,
         true, 'HISTORICAL', true)
    ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
"""

def do_insert(engine, rows: List[dict]) -> int:
    from sqlalchemy import text
    with engine.begin() as conn:
        result = conn.execute(text(_INSERT_SQL), rows)
    return result.rowcount

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Bulk 5-minute candle recovery")
    parser.add_argument("--execute", action="store_true",
                        help="Actually write to DB (default is dry-run)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip chunks already completed in bulk_recovery_progress.json")
    args    = parser.parse_args()
    dry_run = not args.execute

    abort_if_market_hours()

    mode = "dry-run" if dry_run else "execute"
    log.info("Bulk Recovery -- %s", mode.upper())
    log.info("Range: %s to %s  |  MAX_WORKERS=%d  MIN_GAP=%.1fs",
             RECOVERY_START, RECOVERY_END, MAX_WORKERS, MIN_REQUEST_GAP)

    engine = get_engine()
    chunks = make_chunks()
    log.info("Chunks: %d x %d-day windows", len(chunks), CHUNK_DAYS)
    for i, (cs, ce) in enumerate(chunks, 1):
        log.info("  Chunk %d: %s to %s", i, cs, ce)

    # DB scan
    universe = get_universe(engine)
    sessions = get_trading_sessions(engine)
    complete = get_complete_pairs(engine)
    log.info("Universe: %d tickers  |  %d trading sessions in DB",
             len(universe), len(sessions))

    fetch_plan = build_fetch_plan(universe, complete, sessions, chunks)

    prog = Progress(len(fetch_plan), mode)
    if args.resume:
        prog.load_completed()
        fetch_plan = [
            (t, cs, ce) for (t, cs, ce) in fetch_plan
            if not prog.is_done(t, cs, ce)
        ]
        prog.total = prog.done + len(fetch_plan)
        log.info("After resume filter: %d chunks remaining", len(fetch_plan))

    # Dry-run summary
    if dry_run:
        chunk_counts = {}
        for cs, ce in chunks:
            chunk_counts[(cs, ce)] = sum(1 for (_, a, b) in fetch_plan if a == cs and b == ce)

        tickers_needing_fetch = len({t for (t, _, _) in fetch_plan})
        tickers_skipped       = len(universe) - tickers_needing_fetch
        total_skippable_pairs = sum(len(v) for v in complete.values())

        print()
        print("=" * 65)
        print("  BULK RECOVERY -- DRY-RUN PLAN")
        print("=" * 65)
        print(f"  Universe tickers                    {len(universe):>8,}")
        print(f"  Tickers already fully complete      {tickers_skipped:>8,}  (0 calls)")
        print(f"  Tickers needing AngelOne calls      {tickers_needing_fetch:>8,}")
        print(f"  (ticker,session) pairs already done {total_skippable_pairs:>8,}  (skip)")
        print()
        print(f"  Total AngelOne API calls            {len(fetch_plan):>8,}")
        for i, ((cs, ce), count) in enumerate(chunk_counts.items(), 1):
            sess_in_chunk = [d for d in sessions if cs <= d <= ce]
            print(f"    Chunk {i} ({cs} to {ce})  "
                  f"{count:>6,} calls  ({len(sess_in_chunk)} sessions)")
        print()
        est_sec = len(fetch_plan) / MAX_WORKERS * MIN_REQUEST_GAP
        print(f"  Estimated runtime @ {MAX_WORKERS} workers, {MIN_REQUEST_GAP}s gap:")
        print(f"    ~{est_sec/60:.0f} minutes  ({est_sec/3600:.1f} hrs)")
        print()
        print("  Run with --execute to start recovery.")
        print("=" * 65)
        return

    # Login
    from angelone_service import angelone_service as ao_svc
    from historical_service import historical_service as hist_svc
    ao_svc.load_instruments()
    if not hist_svc.login():
        log.error("AngelOne login failed.")
        sys.exit(1)

    log.info("Starting fetch: %d chunks, %d workers, %.1fs min gap",
             len(fetch_plan), MAX_WORKERS, MIN_REQUEST_GAP)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(fetch_chunk, t, cs, ce, hist_svc): (t, cs, ce)
            for (t, cs, ce) in fetch_plan
        }

        for future in as_completed(futures):
            ticker, cs, ce, candles, error = future.result()
            n_inserted = 0

            if error:
                if "PERMANENT" not in error:
                    log.warning("[%d/%d] %s %s->%s: %s",
                                prog.done + 1, prog.total, ticker, cs, ce, error)
            elif candles:
                rows = [
                    {
                        "ticker":    ticker,
                        "timestamp": _norm(c["timestamp"]),
                        "open":  c["open"],  "high": c["high"],
                        "low":   c["low"],   "close": c["close"],
                        "volume": c["volume"],
                    }
                    for c in candles
                ]
                n_inserted = do_insert(engine, rows)
                if n_inserted > 0:
                    log.info("[%d/%d] %s %s->%s: +%d inserted (of %d from provider)",
                             prog.done + 1, prog.total, ticker, cs, ce,
                             n_inserted, len(candles))

            prog.tick(ticker, cs, ce, len(candles) if candles else 0, n_inserted, error)

            if prog.done % 200 == 0:
                elapsed = time.monotonic() - prog.start
                log.info(
                    "Progress: %d/%d (%.1f%%) | +%d inserted | "
                    "perm_err=%d retry_err=%d | elapsed=%ds",
                    prog.done, prog.total,
                    prog.done / prog.total * 100 if prog.total else 0,
                    prog.rows_inserted,
                    prog.permanent_errors, prog.retry_exhausted,
                    round(elapsed),
                )

    elapsed = time.monotonic() - prog.start
    print()
    print("=" * 65)
    print("  BULK RECOVERY COMPLETE")
    print("=" * 65)
    print(f"  Chunks processed                    {prog.done:>10,}")
    print(f"  Candles from AngelOne               {prog.candles_from_provider:>10,}")
    print(f"  Rows inserted                       {prog.rows_inserted:>10,}")
    print(f"  Permanent errors (ticker not found) {prog.permanent_errors:>10,}")
    print(f"  Retryable exhausted                 {prog.retry_exhausted:>10,}")
    print(f"  Elapsed                             {elapsed/60:>9.1f} min")
    print("=" * 65)
    print()
    log.info("Progress file: %s", PROGRESS_FILE)


if __name__ == "__main__":
    main()
