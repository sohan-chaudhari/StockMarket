"""
Pure planning layer for the scoped 1D-only migration entrypoint
(run_1d_migration.py). Every function here is either a pure function or a
read-only DB query -- nothing in this module calls Angel One or writes
anything, which is exactly why it's the piece exercised by dry-run.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from migration.identity import TickerIdentity, ShadowedListing, resolve_ticker_universe
from migration.config import compute_1d_required_range, chunk_date_range


@dataclass
class TickerFetchPlan:
    identity: TickerIdentity
    existing_start: Optional[date]
    required_start: date
    fetch_range: Optional[Tuple[date, date]]   # None = already satisfies the 730-day requirement
    chunks: List[Tuple[date, date]] = field(default_factory=list)
    token_resolvable: Optional[bool] = None    # None = not checked

    @property
    def needs_fetch(self) -> bool:
        return self.fetch_range is not None


@dataclass
class MigrationPlan:
    required_start: date
    today: date
    retention_days: int
    identities: List[TickerIdentity]
    shadowed: List[ShadowedListing]
    ticker_plans: List[TickerFetchPlan]

    @property
    def complete_tickers(self) -> List[TickerFetchPlan]:
        """Already satisfy the 730-day requirement -- no fetch needed."""
        return [p for p in self.ticker_plans if not p.needs_fetch]

    @property
    def no_history_tickers(self) -> List[TickerFetchPlan]:
        return [p for p in self.ticker_plans if p.needs_fetch and p.existing_start is None]

    @property
    def partial_history_tickers(self) -> List[TickerFetchPlan]:
        return [p for p in self.ticker_plans if p.needs_fetch and p.existing_start is not None]

    @property
    def unresolvable_token_tickers(self) -> List[TickerFetchPlan]:
        return [p for p in self.ticker_plans if p.token_resolvable is False]

    @property
    def total_chunks(self) -> int:
        return sum(len(p.chunks) for p in self.ticker_plans if p.needs_fetch)

    @property
    def total_expected_rows(self) -> int:
        """Rough estimate: one row per calendar day in each ticker's fetch
        range (an overestimate vs. actual trading-session count, which is
        fine for a capacity estimate -- never used for the real fetch
        range itself, which is always the exact calendar-day range)."""
        total = 0
        for p in self.ticker_plans:
            if p.needs_fetch:
                start, end = p.fetch_range
                total += (end - start).days + 1
        return total

    def estimate_requests(self) -> int:
        return self.total_chunks

    def estimate_runtime_seconds(self, requests_per_second: float, workers: int) -> float:
        """Runtime is bounded by the GLOBAL request rate, not by worker count.

        BUG FIXED HERE: this used to return requests / (rps * workers),
        which implied more workers meant proportionally less wall-clock
        time. They do not. BatchDownloader builds exactly ONE
        AngelOneFetchManager (batch_downloader.py, __init__), holding ONE
        RateLimiter whose acquire() serializes every worker through a single
        mutex and a single _last_call timestamp. So N workers all queue
        behind the same 1/requests_per_second spacing: throughput is
        requests_per_second in total, no matter how many threads ask.

        Workers still help hide per-request latency and DB/validation work
        between calls, but they cannot exceed the global rate cap -- so the
        honest lower bound on wall-clock time is requests / rps. Multiplying
        by workers understated the full-universe run by 2x, which is exactly
        the number a go/no-go decision rests on.

        `workers` is retained in the signature (callers pass it, and it
        documents the intent) but deliberately does not divide the estimate.
        """
        return self.estimate_requests() / max(requests_per_second, 0.0001)


def build_1d_migration_plan(
    identity_rows: List[Dict],
    existing_1d_start_by_ticker: Dict[str, date],
    today: date,
    retention_days: int,
    max_chunk_days: int,
    token_resolver: Optional[Callable[[str, str], bool]] = None,
) -> MigrationPlan:
    """
    Pure planning function -- no DB access, no network access. Callers
    (run_1d_migration.py) do the DB reads and pass plain data in; this
    keeps the actual planning logic (the part with real bugs to catch)
    fully unit-testable without a database or mocks of one.

    token_resolver, if given, must itself make ZERO network calls (see
    run_1d_migration.py's dry-run wiring, which uses
    angelone_service.get_token() directly WITHOUT calling load_instruments()
    first -- get_token() never triggers a download on its own, only
    load_instruments() does).
    """
    identities, shadowed = resolve_ticker_universe(identity_rows)
    required_start = today - timedelta(days=retention_days)

    ticker_plans: List[TickerFetchPlan] = []
    for identity in identities:
        existing_start = existing_1d_start_by_ticker.get(identity.ticker)
        fetch_range = compute_1d_required_range(required_start, today, existing_start)
        chunks = chunk_date_range(fetch_range[0], fetch_range[1], max_chunk_days) if fetch_range else []
        token_ok = token_resolver(identity.ticker, identity.exchange) if token_resolver else None
        ticker_plans.append(TickerFetchPlan(
            identity=identity,
            existing_start=existing_start,
            required_start=required_start,
            fetch_range=fetch_range,
            chunks=chunks,
            token_resolvable=token_ok,
        ))

    return MigrationPlan(
        required_start=required_start,
        today=today,
        retention_days=retention_days,
        identities=identities,
        shadowed=shadowed,
        ticker_plans=ticker_plans,
    )


def audit_existing_1d_coverage(db, tickers: Optional[List[str]] = None) -> Dict[str, date]:
    """Read-only: earliest existing 1D candle date per ticker. `tickers`
    narrows the query (avoids scanning the whole table when only a known
    active universe matters); omit to cover every ticker with 1D data."""
    from sqlalchemy import text, bindparam

    if tickers:
        stmt = text("""
            SELECT ticker, MIN(timestamp) FROM candles
            WHERE timeframe = '1D' AND ticker IN :tickers
            GROUP BY ticker
        """).bindparams(bindparam("tickers", expanding=True))
        rows = db.execute(stmt, {"tickers": list(tickers)}).fetchall()
    else:
        rows = db.execute(text("""
            SELECT ticker, MIN(timestamp) FROM candles WHERE timeframe = '1D' GROUP BY ticker
        """)).fetchall()

    result = {}
    for ticker, ts in rows:
        result[ticker] = ts.date() if hasattr(ts, "date") else ts
    return result
