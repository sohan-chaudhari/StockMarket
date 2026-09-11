"""
Additive gap repair for 1D candles.
======================================================================
A repair target (from migration.completeness) is an arbitrary gap that can
sit anywhere in a ticker's history -- not "everything older than what's
already there", which is what batch_downloader.py's normal per-ticker
narrowing computes. This module fetches exactly the explicit missing
ranges reported by the completeness check instead.

It is not a second migration path: it reuses the same AngelOneFetchManager
(retry/backoff/rate-limit), the same MigrationValidator, and the same
staging table + additive promotion (migration.promotion.promote_1d_candles)
as the main pipeline. It never touches production `candles` directly.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from migration.config import MigrationConfig, chunk_date_range
from migration.fetch_manager import AngelOneFetchManager
from migration.validator import MigrationValidator


@dataclass
class RepairResult:
    ticker: str
    ranges_requested: List[Tuple[date, date]]
    chunks_attempted: int = 0
    chunks_failed: int = 0
    candles_fetched: int = 0
    candles_staged: int = 0
    validation_passed: bool = True
    validation_summary: str = ""
    errors: List[str] = field(default_factory=list)


def repair_ticker_gaps(
    db,
    cfg: MigrationConfig,
    fetch_mgr: AngelOneFetchManager,
    validator: MigrationValidator,
    ticker: str,
    exchange: str,
    missing_ranges: List[Tuple[date, date]],
    angel_interval: str = "ONE_DAY",
    timeframe: str = "1D",
) -> RepairResult:
    """Fetches exactly `missing_ranges` (each an inclusive (start, end) date
    pair) for `ticker`, validates them, and inserts additively into the
    staging table -- ON CONFLICT DO NOTHING, so re-running this for a range
    that already has some rows staged is always safe. Does NOT promote or
    reconcile -- callers run migration.promotion.promote_1d_candles /
    reconcile_1d_promotion afterward, same as the main pipeline."""
    from sqlalchemy import text

    result = RepairResult(ticker=ticker, ranges_requested=list(missing_ranges))
    if not missing_ranges:
        return result

    # Same per-tier chunk width the normal downloader uses (1D overrides the
    # global 90-day default with its verified 730-day span); falls back to the
    # global default when no matching tier is configured.
    _tier = next((t for t in cfg.tiers if t.timeframe == timeframe), None)
    chunk_days = (_tier.effective_max_date_range_days(cfg.max_date_range_days)
                  if _tier is not None else cfg.max_date_range_days)

    all_candles: List[Dict] = []
    for start, end in missing_ranges:
        for chunk_start, chunk_end in chunk_date_range(start, end, chunk_days):
            result.chunks_attempted += 1
            success, candles, err, attempts = fetch_mgr.fetch_with_retry(
                ticker, angel_interval, chunk_start, chunk_end, exchange=exchange,
            )
            if not success:
                result.chunks_failed += 1
                result.errors.append(f"{chunk_start}..{chunk_end}: {err}")
                continue
            all_candles.extend(candles)

    result.candles_fetched = len(all_candles)
    if not all_candles:
        result.validation_summary = "no candles fetched (all chunks empty or failed)"
        return result

    overall_start = min(r[0] for r in missing_ranges)
    overall_end = max(r[1] for r in missing_ranges)
    val = validator.validate_source(all_candles, timeframe, (overall_start, overall_end))
    result.validation_passed = val.passed
    result.validation_summary = val.summary()
    if not val.passed:
        result.errors.append(f"Validation failed: {val.summary()}")
        return result

    staged = 0
    for c in all_candles:
        ts = c["timestamp"]
        insert_ts = ts.replace(tzinfo=None) if isinstance(ts, datetime) and ts.tzinfo else ts
        stmt = text(f"""
            INSERT INTO {cfg.staging_table} (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
            VALUES (:ticker, :timeframe, :ts, :open, :high, :low, :close, :volume, TRUE, 'ANGELONE', TRUE)
            ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING
        """)
        r = db.execute(stmt, {
            "ticker": ticker,
            "timeframe": timeframe,
            "ts": insert_ts,
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": int(c.get("volume", 0)),
        })
        if r.rowcount > 0:
            staged += 1
    db.commit()
    result.candles_staged = staged
    return result
