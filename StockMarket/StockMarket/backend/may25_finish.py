"""
May 25 Finish — Targeted retry for the 24 rate-limited tickers.

During the --execute run the AngelOne rate-limit quota was exhausted
mid-run for a cluster of alphabetically adjacent BAL*/BAN* tickers.
All 24 exhausted their 3 retries (5s / 15s / 30s).  This script retries
only those tickers with MAX_WORKERS=1 and a longer gap so the quota has
time to reset between calls.

Usage:
    python may25_finish.py          # dry-run (default, no writes)
    python may25_finish.py --execute
"""

import sys, os, time, json, pathlib, threading, argparse, logging
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

backend_dir = pathlib.Path(__file__).parent.resolve()
sys.path.insert(0, str(backend_dir))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(dotenv_path=backend_dir / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("may25_finish")

# ── Constants ─────────────────────────────────────────────────────────────────
TARGET_DATE     = date(2026, 5, 25)
INTERVAL        = "FIVE_MINUTE"
EXCHANGE        = "NSE"
MAX_WORKERS     = 1          # single-threaded — no concurrent burst
MIN_REQUEST_GAP = 1.5        # 1.5 s between requests (~0.67 req/s)
MAX_RETRIES     = 3
RETRY_DELAYS    = [60, 120, 300]   # much longer delays; lets quota window reset
IST             = timezone(timedelta(hours=5, minutes=30))

# Rate gate (same mechanism as may25_recovery.py)
_rate_lock     = threading.Lock()
_last_api_call = [0.0]

def _rate_gate():
    with _rate_lock:
        now  = time.monotonic()
        wait = MIN_REQUEST_GAP - (now - _last_api_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_api_call[0] = time.monotonic()

# ── Ticker list ───────────────────────────────────────────────────────────────
# 10 confirmed rate-limited from execute output + 12 alphabetically adjacent
# ones that are likely also rate-limited (same burst window).
# Already-complete tickers (75 candles) are skipped at runtime via DB check.
SUSPECT_TICKERS = [
    # Confirmed from execute error output
    "BALAJITELE", "BALAXI",    "BALCO",      "BALKRISHNA",
    "BALPHARMA",  "BALUFORGE", "BANARBEADS", "BANARISUG",
    "BANG",       "BANK10ADD",
    # Alphabetically adjacent — also processed in the same rate-limit window
    "BALAJEE",    "BALAJIPHOS",
    "BANKA",      "BANKADD",
    "BANKBETA",   "BANKBETF",
    "BANKETF",    "BANKPSU",
    "BANSALWIRE", "BANSWRAS",
    # Belt-and-suspenders: a few just past BANSWRAS
    "BARBEQUE",   "BASF",
    "BASML",      "BATA",
]

def get_engine():
    from sqlalchemy import create_engine
    url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:medikart%403145@localhost:5432/stock_data"
    )
    return create_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True)

def get_existing_may25(engine, tickers: List[str]) -> Dict[str, set]:
    from sqlalchemy import text
    result: Dict[str, set] = {}
    ds = datetime(2026, 5, 25, 0, 0, 0)
    de = datetime(2026, 5, 26, 0, 0, 0)
    placeholders = ", ".join(f":t{i}" for i in range(len(tickers)))
    params = {f"t{i}": t for i, t in enumerate(tickers)}
    params["ds"] = ds; params["de"] = de
    sql = f"""
        SELECT ticker, timestamp FROM candles
        WHERE timeframe='5m' AND timestamp >= :ds AND timestamp < :de
          AND ticker IN ({placeholders})
    """
    with engine.connect() as conn:
        for ticker, ts in conn.execute(text(sql), params).fetchall():
            naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
            result.setdefault(ticker, set()).add(naive)
    return result

def _norm(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo else ts

def fetch_one(ticker: str, hist_svc) -> Tuple[str, List[dict], Optional[str]]:
    from historical_service import HistoricalFetchError
    for attempt in range(MAX_RETRIES):
        try:
            _rate_gate()
            candles = hist_svc.get_historical_candles(
                ticker=ticker, interval=INTERVAL,
                from_date=TARGET_DATE, to_date=TARGET_DATE,
                exchange=EXCHANGE, raise_on_error=True,
            )
            return (ticker, candles, None)
        except HistoricalFetchError as exc:
            if not exc.retryable:
                return (ticker, [], f"PERMANENT: {exc}")
            if attempt < MAX_RETRIES - 1:
                log.warning("Rate limit on %s (attempt %d) — waiting %ds",
                            ticker, attempt + 1, RETRY_DELAYS[attempt])
                time.sleep(RETRY_DELAYS[attempt])
            else:
                return (ticker, [], f"RETRYABLE_EXHAUSTED: {exc}")
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
            else:
                return (ticker, [], f"EXCEPTION: {exc}")
    return (ticker, [], "UNKNOWN")

def abort_if_market_hours():
    now = datetime.now(tz=IST)
    if now.hour == 9 and now.minute >= 0 or 10 <= now.hour <= 15:
        log.error("Market hours (09:00-16:00 IST). Aborting.")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    dry_run = not args.execute

    abort_if_market_hours()

    log.info("May 25 FINISH — %s mode", "DRY-RUN" if dry_run else "EXECUTE")
    log.info("Targeting %d suspect tickers (MAX_WORKERS=1, gap=%.1fs)",
             len(SUSPECT_TICKERS), MIN_REQUEST_GAP)
    log.info("Retry delays if rate-limited: %s seconds", RETRY_DELAYS)

    engine = get_engine()
    existing = get_existing_may25(engine, SUSPECT_TICKERS)

    # Skip tickers already complete
    todo = [t for t in SUSPECT_TICKERS if len(existing.get(t, set())) < 75]
    skipped_complete = len(SUSPECT_TICKERS) - len(todo)
    log.info("Skipping %d already-complete. Fetching %d tickers.",
             skipped_complete, len(todo))

    if not todo:
        log.info("All suspect tickers already complete. Nothing to do.")
        return

    from angelone_service import angelone_service as ao_svc
    from historical_service import historical_service as hist_svc
    ao_svc.load_instruments()
    if not hist_svc.login():
        log.error("AngelOne login failed.")
        sys.exit(1)

    log.info("Pausing 10s before first API call to let rate-limit quota recover...")
    time.sleep(10)

    # Fetch one by one (MAX_WORKERS=1)
    results: Dict[str, Tuple[List[dict], Optional[str]]] = {}
    for i, ticker in enumerate(todo, 1):
        log.info("[%d/%d] Fetching %s ...", i, len(todo), ticker)
        ticker_res, candles, error = fetch_one(ticker, hist_svc)
        results[ticker] = (candles, error)
        existing_ts = existing.get(ticker, set())
        if error:
            log.warning("  %s -> %s", ticker, error)
        elif candles:
            provider_ts = {_norm(c["timestamp"]) for c in candles}
            missing = provider_ts - existing_ts
            log.info("  %s -> %d from provider, %d already in DB, %d missing",
                     ticker, len(candles), len(provider_ts & existing_ts), len(missing))
        else:
            log.info("  %s -> no data from AngelOne", ticker)

    # Build insert plan
    from sqlalchemy import text
    insert_plan: Dict[str, List[dict]] = {}
    total_insert = 0
    for ticker in todo:
        candles, error = results.get(ticker, ([], None))
        if not candles or error:
            continue
        existing_ts = existing.get(ticker, set())
        new_rows = []
        for c in candles:
            ts = _norm(c["timestamp"])
            if ts not in existing_ts:
                new_rows.append({
                    "ticker": ticker, "timestamp": ts,
                    "open": c["open"], "high": c["high"],
                    "low": c["low"],  "close": c["close"],
                    "volume": c["volume"],
                })
        if new_rows:
            insert_plan[ticker] = new_rows
            total_insert += len(new_rows)

    # Report
    print()
    print("=" * 60)
    print("  MAY 25 FINISH REPORT -", "DRY-RUN" if dry_run else "EXECUTE")
    print("=" * 60)
    print(f"  Suspect tickers targeted        {len(SUSPECT_TICKERS):>6}")
    print(f"  Already complete (skipped)      {skipped_complete:>6}")
    print(f"  Fetched from AngelOne           {len(todo):>6}")
    print(f"  Tickers with missing candles    {len(insert_plan):>6}")
    print(f"  Candles to insert               {total_insert:>6}")
    print("-" * 60)

    for ticker, rows in insert_plan.items():
        print(f"  {ticker:<25} {len(rows):>3} missing candles")
    print()

    if dry_run:
        print("  DRY-RUN complete. Pass --execute to insert.")
        print("=" * 60)
        return

    if not insert_plan:
        print("  Nothing to insert. All suspect tickers already complete.")
        print("=" * 60)
        return

    # Execute inserts
    inserted = 0
    sql = text("""
        INSERT INTO candles
            (ticker, timeframe, timestamp, open, high, low, close, volume,
             is_completed, data_source, is_backfilled)
        VALUES
            (:ticker, '5m', :timestamp, :open, :high, :low, :close, :volume,
             true, 'HISTORICAL', true)
        ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
    """)

    for ticker, rows in insert_plan.items():
        with engine.begin() as conn:
            result = conn.execute(sql, rows)
            inserted += result.rowcount
        log.info("  Inserted %d candles for %s", result.rowcount, ticker)

    print(f"  Rows actually inserted          {inserted:>6}")
    print("=" * 60)

    # Post-insert DB state
    with engine.connect() as conn:
        after = conn.execute(text("""
            SELECT COUNT(DISTINCT CASE WHEN cnt >= 75 THEN ticker END),
                   COUNT(DISTINCT CASE WHEN cnt < 75 THEN ticker END)
            FROM (
                SELECT ticker, COUNT(*) as cnt FROM candles
                WHERE timeframe='5m'
                  AND timestamp >= '2026-05-25 00:00:00'
                  AND timestamp <  '2026-05-26 00:00:00'
                  AND ticker = ANY(:tickers)
                GROUP BY ticker
            ) t
        """), {"tickers": SUSPECT_TICKERS}).fetchone()
    print(f"\n  Suspect tickers now complete:   {after[0]:>6}")
    print(f"  Suspect tickers still partial:  {after[1]:>6}")
    print("=" * 60)

if __name__ == "__main__":
    main()
