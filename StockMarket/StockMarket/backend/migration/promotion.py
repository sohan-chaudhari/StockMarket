"""
Staging -> production promotion for the 1D migration, ONLY.
======================================================================
AUDIT FINDING this module exists to fix: the only promotion mechanism found
anywhere in the repository is run_migration.py's _swap_tables():

    ALTER TABLE candles RENAME TO candles_old_<ts>
    ALTER TABLE candles_migration RENAME TO candles

That is a wholesale table replacement -- it discards EVERY existing
timeframe's data (5m/15m/30m/1h/4h/1W/1M, every ticker) and replaces the
entire `candles` table with only whatever the staging table happens to
contain from that run. It is correct for a from-scratch full rebuild, and is
NOT safe for an incremental 1D gap-fill that must leave all other existing
production data completely untouched. Do not use it for this migration.

This module is the additive alternative:
  - reads ONLY timeframe='1D' rows from the staging table (never touches
    other timeframes even if they happen to be present in staging),
  - re-validates OHLCV/timestamp per row (defense in depth beyond whatever
    validation ran at fetch time -- staging data should not be blindly
    trusted just because it passed validation once),
  - INSERTs only rows whose (ticker, timeframe, timestamp) does not already
    exist in production `candles` -- existing rows are NEVER updated, so a
    staging row can never overwrite/corrupt already-good production data,
  - commits per batch, and is idempotent by construction: a crash mid-run
    (or a process restart, or simply running it twice) is always safe to
    resume by calling promote_1d_candles() again -- rows already promoted
    are re-detected via the existence check and skipped, not re-inserted or
    re-updated. No new "promotion status" tracking column was added to
    migration_jobs for this; idempotent-by-construction promotion is the
    minimum safe mechanism and needs no additional state to be crash-safe.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple


@dataclass
class RejectedRow:
    ticker: str
    timestamp: object
    reason: str


@dataclass
class PromotionResult:
    staged_rows_seen: int = 0
    already_in_production: int = 0
    invalid_rejected: int = 0
    promoted: int = 0
    rejected: List[RejectedRow] = field(default_factory=list)

    @property
    def total_accounted(self) -> int:
        return self.already_in_production + self.invalid_rejected + self.promoted


def _is_finite_number(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _coerce_timestamp(ts) -> Optional[datetime]:
    """Normalize a raw-SQL SELECT's timestamp value to a real datetime.

    Different DBAPI drivers hand back different Python types for the exact
    same underlying column when read through a raw `text()` SELECT rather
    than the ORM's typed Column: psycopg2 (production, Postgres) already
    returns a native datetime; sqlite3 (used by this module's own test
    suite, and possibly other future callers) returns a plain string. Rather
    than assume one driver's behavior, this coerces on the way in and lets
    validate_candle_row's `isinstance(ts, datetime)` check remain a strict,
    single source of truth -- a value that still fails to parse here is
    correctly rejected there as malformed, not silently accepted."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None


def validate_candle_row(row: Dict) -> Optional[str]:
    """Pure function, no DB access: returns None if the row is safe to
    promote, else a human-readable rejection reason. Reused by both the
    fetch-time validator (MigrationValidator, aggregator.validate_ohlc) and
    here at promotion time -- deliberately re-checked rather than trusted,
    since staging data could in principle be stale or hand-edited between
    the two steps."""
    ts = row.get("timestamp")
    if not isinstance(ts, datetime):
        return f"timestamp is not a datetime: {ts!r}"

    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    for name, val in (("open", o), ("high", h), ("low", l), ("close", c)):
        if val is None or not _is_finite_number(val):
            return f"{name} is not a finite number: {val!r}"

    v = row.get("volume")
    if v is None or not _is_finite_number(v):
        return f"volume is not a finite number: {v!r}"
    if float(v) < 0:
        return f"volume is negative: {v}"

    o, h, l, c = float(o), float(h), float(l), float(c)
    if not (h >= o and h >= c and l <= o and l <= c and h >= l):
        return f"invalid OHLC relationship: O={o} H={h} L={l} C={c}"

    return None


def promote_1d_candles(
    db,
    staging_table: str = "candles_migration",
    batch_size: int = 2000,
) -> PromotionResult:
    """
    Promote validated 1D rows from `staging_table` into production `candles`.
    Additive only -- see module docstring. `db` is a live SQLAlchemy Session
    (or session-like object exposing .execute/.query/.commit/.rollback);
    tests pass an in-memory SQLite session, production passes the real
    Postgres SessionLocal() -- this function contains no Postgres-specific
    SQL, so the exact same code path is exercised by both.
    """
    from models import Candle
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    result = PromotionResult()
    offset = 0

    while True:
        staged_rows = db.execute(text(f"""
            SELECT ticker, timestamp, open, high, low, close, volume
            FROM {staging_table}
            WHERE timeframe = '1D'
            ORDER BY ticker, timestamp
            LIMIT :limit OFFSET :offset
        """), {"limit": batch_size, "offset": offset}).fetchall()

        if not staged_rows:
            break
        offset += len(staged_rows)
        result.staged_rows_seen += len(staged_rows)

        candidates = []
        for r in staged_rows:
            row = {
                "ticker": r[0], "timestamp": _coerce_timestamp(r[1]), "open": r[2],
                "high": r[3], "low": r[4], "close": r[5], "volume": r[6],
            }
            reason = validate_candle_row(row)
            if reason:
                result.invalid_rejected += 1
                result.rejected.append(RejectedRow(ticker=row["ticker"], timestamp=row["timestamp"], reason=reason))
                continue
            candidates.append(row)

        if not candidates:
            continue

        tickers_in_batch = {c["ticker"] for c in candidates}
        existing = db.query(Candle.ticker, Candle.timestamp).filter(
            Candle.timeframe == "1D",
            Candle.ticker.in_(tickers_in_batch),
        ).all()
        existing_keys = {(t, ts) for t, ts in existing}

        to_insert = [c for c in candidates if (c["ticker"], c["timestamp"]) not in existing_keys]
        result.already_in_production += len(candidates) - len(to_insert)

        if not to_insert:
            continue

        try:
            db.bulk_insert_mappings(Candle, [
                {
                    "ticker": c["ticker"], "timeframe": "1D", "timestamp": c["timestamp"],
                    "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                    "volume": int(c["volume"]), "is_completed": True,
                    "data_source": "ANGELONE", "is_backfilled": True,
                }
                for c in to_insert
            ])
            db.commit()
            result.promoted += len(to_insert)
        except IntegrityError:
            # A genuine race (e.g. a concurrent writer inserted the same key
            # between our existence check and this insert) -- fall back to
            # per-row, so one collision doesn't lose the whole batch. Each
            # row is its own transaction; a true duplicate is harmless.
            db.rollback()
            promoted_in_fallback = 0
            for c in to_insert:
                try:
                    db.execute(text(f"""
                        INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close, volume,
                                              is_completed, data_source, is_backfilled)
                        VALUES (:ticker, '1D', :timestamp, :open, :high, :low, :close, :volume,
                                TRUE, 'ANGELONE', TRUE)
                    """), c)
                    db.commit()
                    promoted_in_fallback += 1
                except IntegrityError:
                    db.rollback()
                    result.already_in_production += 1
            result.promoted += promoted_in_fallback
        except Exception:
            db.rollback()
            raise

    return result


@dataclass
class ReconciliationReport:
    staging_valid_rows: int
    staging_invalid_rows: int
    production_rows_for_staged_tickers: int
    tickers_checked: int
    tickers_fully_matched: int
    mismatched_tickers: List[str] = field(default_factory=list)


def reconcile_1d_promotion(db, staging_table: str = "candles_migration") -> ReconciliationReport:
    """Read-only audit: for every ticker present in staging (1D only), compare
    the count of valid staged rows against the count of matching production
    rows. Does not modify anything -- a verification step, not a gate."""
    from sqlalchemy import text, bindparam

    staged = db.execute(text(f"""
        SELECT ticker, timestamp, open, high, low, close, volume
        FROM {staging_table} WHERE timeframe = '1D'
    """)).fetchall()

    valid_by_ticker: Dict[str, set] = {}
    invalid_count = 0
    for r in staged:
        row = {"ticker": r[0], "timestamp": _coerce_timestamp(r[1]), "open": r[2], "high": r[3], "low": r[4], "close": r[5], "volume": r[6]}
        if validate_candle_row(row):
            invalid_count += 1
            continue
        valid_by_ticker.setdefault(row["ticker"], set()).add(row["timestamp"])

    # DB-06: one batched query for every staged ticker's production rows
    # instead of one query per ticker -- this runs against the full
    # migration universe (thousands of tickers).
    tickers = list(valid_by_ticker.keys())
    prod_ts_by_ticker: Dict[str, set] = {t: set() for t in tickers}
    if tickers:
        stmt = text("""
            SELECT ticker, timestamp FROM candles WHERE ticker IN :tickers AND timeframe = '1D'
        """).bindparams(bindparam("tickers", expanding=True))
        prod_rows = db.execute(stmt, {"tickers": tickers}).fetchall()
        for r in prod_rows:
            prod_ts_by_ticker[r[0]].add(_coerce_timestamp(r[1]))

    mismatched = []
    total_prod_rows = 0
    for ticker, staged_ts in valid_by_ticker.items():
        prod_ts = prod_ts_by_ticker.get(ticker, set())
        total_prod_rows += len(prod_ts)
        if not staged_ts.issubset(prod_ts):
            mismatched.append(ticker)

    return ReconciliationReport(
        staging_valid_rows=sum(len(v) for v in valid_by_ticker.values()),
        staging_invalid_rows=invalid_count,
        production_rows_for_staged_tickers=total_prod_rows,
        tickers_checked=len(valid_by_ticker),
        tickers_fully_matched=len(valid_by_ticker) - len(mismatched),
        mismatched_tickers=mismatched,
    )
