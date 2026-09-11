"""
Intraday 5-minute candle prefill service.

Fills any 5m candles missing from the `candles` table for all active NSE
tickers by calling the Angel One historical API once per trading day at/after
16:30 IST (market has been closed for at least 1 hour).

Safety invariants — never violated by this module:
  - Inserts use ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING.
  - No existing candle is ever overwritten (DO NOTHING, not DO UPDATE).
  - data_source = 'HISTORICAL', is_backfilled = True for every inserted row.
  - Timestamps are stored as IST-naive datetimes, matching the live aggregator.
  - Only reads/writes the canonical `candles` table.
  - Angel One's actual response is authoritative; no candle is manufactured.
"""

import json
import threading
import time
from datetime import date, datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text as sa_text

IST = timezone(timedelta(hours=5, minutes=30))

COMPLETE_CANDLES = 75       # Full NSE session: 09:15-15:25, one slot per 5 min
RATE_GAP_SECONDS = 1.2      # Minimum gap between consecutive AngelOne API calls
RETRY_DELAYS     = [5, 15, 30]  # Back-off (seconds) for retryable errors

# Dashboard indices are not in stock_metadata (they're not NSE stocks) so they
# would be silently skipped by _get_universe(). Include them explicitly so the
# daily prefill and startup catchup always cover them.
DASHBOARD_INDICES = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCAP', 'SMALLCAP']

# ── Rate gate ──────────────────────────────────────────────────────────────
_rate_lock     = threading.Lock()
_last_api_time = 0.0


def _rate_gate() -> None:
    """Enforce minimum gap between consecutive AngelOne historical API calls."""
    global _last_api_time
    with _rate_lock:
        gap = time.monotonic() - _last_api_time
        if gap < RATE_GAP_SECONDS:
            time.sleep(RATE_GAP_SECONDS - gap)
        _last_api_time = time.monotonic()


# ── Provider call with retry ───────────────────────────────────────────────

def _fetch_candles(svc, ticker: str, session_date: date) -> List[Dict]:
    """
    Fetch 5m candles for one ticker on one trading session.

    Retries up to len(RETRY_DELAYS) times for retryable errors.
    Raises HistoricalFetchError immediately on permanent errors (retryable=False)
    or after retries are exhausted.
    Returns [] (not an error) when Angel One confirms no data for that range.
    """
    from historical_service import HistoricalFetchError

    from_dt = datetime(session_date.year, session_date.month, session_date.day, 9, 15)
    to_dt   = datetime(session_date.year, session_date.month, session_date.day, 15, 30)

    last_exc: Optional[HistoricalFetchError] = None
    for attempt, delay in enumerate([0] + list(RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            _rate_gate()
            return svc.get_historical_candles(
                ticker=ticker,
                interval="FIVE_MINUTE",
                from_date=from_dt,
                to_date=to_dt,
                exchange="NSE",
                raise_on_error=True,
            )
        except HistoricalFetchError as exc:
            if not exc.retryable:
                raise  # permanent: caller handles immediately, no retry
            last_exc = exc
            if attempt < len(RETRY_DELAYS):
                print(f"[IntradayPrefill] {ticker} attempt {attempt + 1} retryable: {exc}")
    raise last_exc  # type: ignore[misc]  # exhausted all retries


# ── DB helpers ─────────────────────────────────────────────────────────────

def _get_universe() -> List[str]:
    """Return all active NSE tickers from stock_metadata, plus key indices."""
    from database import SessionLocal
    with SessionLocal() as db:
        rows = db.execute(sa_text(
            "SELECT ticker FROM stock_metadata "
            "WHERE exchange = 'NSE' AND is_active = TRUE "
            "ORDER BY ticker"
        )).fetchall()
    tickers = [r[0] for r in rows]
    # Prepend dashboard indices so they are filled first (high priority)
    existing = set(tickers)
    for idx in reversed(DASHBOARD_INDICES):
        if idx not in existing:
            tickers.insert(0, idx)
    return tickers


def _get_complete_tickers(session_date: date) -> Set[str]:
    """Return tickers that already have >= COMPLETE_CANDLES 5m rows for session_date."""
    from database import SessionLocal
    with SessionLocal() as db:
        rows = db.execute(sa_text("""
            SELECT ticker
            FROM   candles
            WHERE  timeframe        = '5m'
              AND  timestamp::date = :d
            GROUP  BY ticker
            HAVING COUNT(*) >= :n
        """), {"d": session_date, "n": COMPLETE_CANDLES}).fetchall()
    return {r[0] for r in rows}


def _insert_candles(ticker: str, candles: List[Dict]) -> Tuple[int, int]:
    """
    Insert candles with DO NOTHING.  Returns (inserted, existed).

    Timestamps are converted from timezone-aware IST to IST-naive before
    storage, matching the format written by the live 5m aggregator so that
    the unique constraint (ticker, timeframe, timestamp) covers both sources.
    """
    if not candles:
        return 0, 0
    from database import SessionLocal
    inserted = existed = 0
    with SessionLocal() as db:
        for c in candles:
            ts = c["timestamp"]
            # Normalise to IST-naive, matching aggregator.batch_flush_candles().
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                ts = ts.astimezone(IST).replace(tzinfo=None)
            result = db.execute(sa_text("""
                INSERT INTO candles
                    (ticker, timeframe, timestamp,
                     open, high, low, close, volume,
                     is_completed, data_source, is_backfilled)
                VALUES
                    (:ticker, '5m', :timestamp,
                     :open, :high, :low, :close, :volume,
                     true, 'HISTORICAL', true)
                ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING
            """), {
                "ticker":    ticker,
                "timestamp": ts,
                "open":      c["open"],
                "high":      c["high"],
                "low":       c["low"],
                "close":     c["close"],
                "volume":    c["volume"],
            })
            if result.rowcount > 0:
                inserted += 1
            else:
                existed += 1
        db.commit()
    return inserted, existed


# ── Job tracking helpers ───────────────────────────────────────────────────

def _upsert_job_running(session_date: date) -> None:
    """Create or reset the job row for session_date to status=RUNNING."""
    from database import SessionLocal
    with SessionLocal() as db:
        db.execute(sa_text("""
            INSERT INTO intraday_prefill_jobs (session_date, status, started_at)
            VALUES (:d, 'RUNNING', NOW())
            ON CONFLICT (session_date) DO UPDATE
                SET status     = 'RUNNING',
                    started_at = NOW()
        """), {"d": session_date})
        db.commit()


def _update_job_done(session_date: date, stats: Dict) -> None:
    """Persist final stats and mark the job COMPLETED or PARTIAL."""
    from database import SessionLocal
    total_failed = stats.get("failed_perm", 0) + stats.get("failed_retry", 0)
    status = "PARTIAL" if total_failed > 0 else "COMPLETED"
    failed_list = stats.get("failed_tickers", [])
    with SessionLocal() as db:
        db.execute(sa_text("""
            UPDATE intraday_prefill_jobs
            SET    status          = :status,
                   tickers_total   = :total,
                   tickers_skipped = :skipped,
                   tickers_fetched = :fetched,
                   tickers_no_data = :no_data,
                   tickers_partial = :partial,
                   tickers_failed  = :failed,
                   candles_inserted= :inserted,
                   candles_existed = :existed,
                   completed_at    = NOW(),
                   error_detail    = :detail
            WHERE  session_date = :d
        """), {
            "status":   status,
            "total":    stats["total"],
            "skipped":  stats["skipped"],
            "fetched":  stats["fetched"],
            "no_data":  stats["no_data"],
            "partial":  stats["partial"],
            "failed":   total_failed,
            "inserted": stats["inserted"],
            "existed":  stats["existed"],
            "detail":   json.dumps(failed_list[:100]) if failed_list else None,
            "d":        session_date,
        })
        db.commit()


# ── Public entry point ─────────────────────────────────────────────────────

def run_prefill_session(session_date: date) -> Dict:
    """
    Fill missing 5m candles for every active NSE ticker for session_date.
    Idempotent: safe to call multiple times for the same date.
    """
    from historical_service import HistoricalDataService, HistoricalFetchError

    _upsert_job_running(session_date)

    svc = HistoricalDataService()
    if not svc.login():
        raise RuntimeError("[IntradayPrefill] AngelOne login failed")

    universe = _get_universe()
    complete = _get_complete_tickers(session_date)
    to_fetch = [t for t in universe if t not in complete]

    stats: Dict = {
        "total":          len(universe),
        "skipped":        len(universe) - len(to_fetch),
        "fetched":        0,
        "no_data":        0,
        "partial":        0,
        "failed_perm":    0,
        "failed_retry":   0,
        "inserted":       0,
        "existed":        0,
        "failed_tickers": [],
    }

    print(f"[IntradayPrefill] {session_date}: {len(universe)} tickers, "
          f"{stats['skipped']} already complete, {len(to_fetch)} to fetch")

    for i, ticker in enumerate(to_fetch):
        if i and i % 500 == 0:
            pct = 100 * i // len(to_fetch)
            print(f"[IntradayPrefill] Progress: {i}/{len(to_fetch)} ({pct}%), "
                  f"inserted={stats['inserted']} existed={stats['existed']}")
        try:
            candles = _fetch_candles(svc, ticker, session_date)
            if not candles:
                stats["no_data"] += 1
                continue
            stats["fetched"] += 1
            if len(candles) < COMPLETE_CANDLES:
                stats["partial"] += 1
            ins, ex = _insert_candles(ticker, candles)
            stats["inserted"] += ins
            stats["existed"]  += ex
        except HistoricalFetchError as exc:
            cat = "failed_perm" if not exc.retryable else "failed_retry"
            stats[cat] += 1
            stats["failed_tickers"].append(ticker)
        except Exception as exc:
            stats["failed_retry"] += 1
            stats["failed_tickers"].append(ticker)
            print(f"[IntradayPrefill] {ticker} unexpected error: {exc}")

    _update_job_done(session_date, stats)
    total_failed = stats["failed_perm"] + stats["failed_retry"]
    print(f"[IntradayPrefill] Done {session_date}: "
          f"fetched={stats['fetched']} no_data={stats['no_data']} "
          f"inserted={stats['inserted']} existed={stats['existed']} "
          f"failed={total_failed}")
    return stats


# ── One-time startup helpers ───────────────────────────────────────────────

# The date when the 5m ingestion pipeline collapsed (FEED-01 / PREFLT-01).
# All tickers from this date onward may have missing 5m candles.
_HISTORICAL_GAP_START = date(2026, 7, 6)


def cleanup_phantom_slot_candles() -> int:
    """
    Delete rows whose IST time is exactly 15:30 — these are phantom candles
    written by the snap bug (fixed Session 7). NSE closes at 15:30; the last
    valid 5m slot is 15:25. Returns the number of rows deleted.
    Idempotent: safe to call on every startup (deletes 0 if already clean).
    """
    from database import SessionLocal
    with SessionLocal() as db:
        result = db.execute(sa_text("""
            DELETE FROM candles
            WHERE timeframe IN ('5m', '15m')
              AND EXTRACT(HOUR   FROM timestamp + INTERVAL '5 hours 30 minutes') = 15
              AND EXTRACT(MINUTE FROM timestamp + INTERVAL '5 hours 30 minutes') = 30
        """))
        deleted = result.rowcount
        db.commit()
    if deleted:
        print(f"[IntradayPrefill] Phantom cleanup: deleted {deleted} phantom 15:30 candle(s).")
    else:
        print("[IntradayPrefill] Phantom cleanup: no phantom 15:30 candles found — DB already clean.")
    return deleted


def needs_historical_backfill() -> bool:
    """
    True when more than 100 active NSE tickers have zero 5m candles in the
    _HISTORICAL_GAP_START → (today − 7 days) window. Returns False once the
    backfill completes so subsequent startups skip it immediately.
    """
    import database
    from database import SessionLocal
    cutoff = (database.get_ist_now() - timedelta(days=7)).date()
    if cutoff <= _HISTORICAL_GAP_START:
        return False
    with SessionLocal() as db:
        missing = db.execute(sa_text("""
            SELECT COUNT(DISTINCT sm.ticker)
            FROM   stock_metadata sm
            LEFT JOIN (
                SELECT DISTINCT ticker
                FROM   candles
                WHERE  timeframe   = '5m'
                  AND  timestamp::date BETWEEN :from_d AND :to_d
            ) c ON c.ticker = sm.ticker
            WHERE  sm.is_active = TRUE
              AND  sm.exchange  = 'NSE'
              AND  c.ticker IS NULL
        """), {"from_d": _HISTORICAL_GAP_START, "to_d": cutoff}).scalar()
    result = (missing or 0) > 100
    if result:
        print(f"[IntradayPrefill] HistoricalBackfill: {missing} tickers missing 5m data "
              f"in {_HISTORICAL_GAP_START} → {cutoff} — backfill needed.")
    else:
        print(f"[IntradayPrefill] HistoricalBackfill: coverage OK — skipping.")
    return result


def run_historical_backfill() -> None:
    """
    Fill 5m candles for all active NSE tickers from _HISTORICAL_GAP_START to
    7 days ago. Fetches per-ticker across the full range in 60-day chunks
    (much faster than per-session for a large date range). The last 7 days
    are left for catchup_missed_sessions which is session-aware.
    Idempotent: existing candles are never overwritten (DO NOTHING).
    """
    import database
    from database import SessionLocal
    from historical_service import HistoricalDataService

    cutoff = (database.get_ist_now() - timedelta(days=7)).date()
    if cutoff <= _HISTORICAL_GAP_START:
        return

    svc = HistoricalDataService()
    if not svc.login():
        print("[IntradayPrefill] HistoricalBackfill: AngelOne login failed — skipping")
        return

    with SessionLocal() as db:
        rows = db.execute(sa_text(
            "SELECT ticker FROM stock_metadata "
            "WHERE exchange = 'NSE' AND is_active = TRUE ORDER BY ticker"
        )).fetchall()
    universe = [r[0] for r in rows]

    print(f"[IntradayPrefill] HistoricalBackfill: {len(universe)} tickers, "
          f"{_HISTORICAL_GAP_START} → {cutoff}")

    CHUNK_DAYS = 60
    inserted_total = 0
    from datetime import datetime as _dt

    for i, ticker in enumerate(universe):
        if i and i % 200 == 0:
            pct = 100 * i // len(universe)
            print(f"[IntradayPrefill] HistoricalBackfill: {i}/{len(universe)} ({pct}%), "
                  f"{inserted_total:,} candles inserted")
        current = _dt(_HISTORICAL_GAP_START.year, _HISTORICAL_GAP_START.month,
                      _HISTORICAL_GAP_START.day, 9, 15)
        end_dt  = _dt(cutoff.year, cutoff.month, cutoff.day, 15, 30)
        while current <= end_dt:
            chunk_end = min(current + timedelta(days=CHUNK_DAYS), end_dt)
            try:
                _rate_gate()
                candles = svc.get_historical_candles(
                    ticker=ticker,
                    interval="FIVE_MINUTE",
                    from_date=current,
                    to_date=chunk_end,
                    exchange="NSE",
                    raise_on_error=False,
                )
                if candles:
                    ins, _ = _insert_candles(ticker, candles)
                    inserted_total += ins
            except Exception as exc:
                print(f"[IntradayPrefill] HistoricalBackfill: {ticker} error: {exc}")
            current = chunk_end + timedelta(days=1)

    print(f"[IntradayPrefill] HistoricalBackfill: complete — {inserted_total:,} candles inserted.")


def catchup_missed_sessions(lookback_days: int = 7) -> None:
    """
    Called once at startup. Scans the last `lookback_days` calendar days,
    finds any NSE trading sessions whose prefill job is missing or non-COMPLETED,
    and runs run_prefill_session() for each — oldest first.

    This closes the gap when the backend was offline for one or more days so
    the daily scheduler (which only fills *today*) never needs to look back.
    Idempotent: existing candles are never overwritten (DO NOTHING).
    """
    from exchange_calendar import nse_calendar
    import database
    from database import SessionLocal

    today = database.get_ist_now().date()
    missed = []
    for i in range(lookback_days, 0, -1):          # oldest → newest
        d = today - timedelta(days=i)
        if not nse_calendar.is_trading_day(d):
            continue
        with SessionLocal() as db:
            row = db.execute(sa_text(
                "SELECT status FROM intraday_prefill_jobs WHERE session_date = :d"
            ), {"d": d}).first()
        if row is None or row[0] != "COMPLETED":
            missed.append(d)

    if not missed:
        print(f"[IntradayPrefill] Catchup: all sessions complete in last {lookback_days} days — nothing to do.")
        return

    print(f"[IntradayPrefill] Catchup: {len(missed)} missed session(s) → {missed}")
    for d in missed:
        print(f"[IntradayPrefill] Catchup: filling {d} …")
        try:
            run_prefill_session(d)
        except Exception as exc:
            print(f"[IntradayPrefill] Catchup: {d} failed — {exc}")


def prefill_due_today() -> bool:
    """
    True when today is an NSE trading day AND today's prefill has not yet
    completed (status != 'COMPLETED').  Handles non-trading days, weekends,
    and NSE holidays via nse_calendar.
    """
    from exchange_calendar import nse_calendar
    import database
    from database import SessionLocal

    today = database.get_ist_now().date()
    if not nse_calendar.is_trading_day(today):
        return False
    with SessionLocal() as db:
        row = db.execute(sa_text(
            "SELECT status FROM intraday_prefill_jobs WHERE session_date = :d"
        ), {"d": today}).first()
    return row is None or row[0] != "COMPLETED"
