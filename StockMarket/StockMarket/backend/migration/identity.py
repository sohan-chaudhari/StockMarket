"""
Ticker/exchange identity resolution for the historical migration.
======================================================================
Root cause this module fixes: MigrationOrchestrator._get_all_tickers()
previously ran `SELECT DISTINCT ticker FROM stock_metadata WHERE is_active`
-- dropping the `exchange` column entirely before a single Angel One call was
ever made. `candles`'s schema (models.py, uix_candle_key = ticker+timeframe+
timestamp) has no exchange column, so two active listings that share a
ticker string (e.g. a stock active on both NSE and BSE) cannot both be
written into `candles` under that string -- one would silently overwrite
the other via the existing ON CONFLICT upsert used everywhere in this app.

What the app ALREADY does, verified by reading the code (not invented here):
angelone_service.get_token(ticker, exchange: str = "NSE") -- the default
parameter is NSE, and every call site in the live app that doesn't pass an
explicit exchange already gets NSE. That is the app's existing, implicit
canonical-security convention. This module makes it explicit and applies it
consistently to the migration's ticker universe, rather than inventing a new
policy: when a symbol is active on both NSE and BSE, NSE is canonical; the
shadowed BSE listing is reported, not silently dropped.

This does NOT change `candles`'s schema. Adding an `exchange` column there
is the more complete long-term fix (see the migration guide/report this
module ships with) but is a live-table DDL change with many existing
consumers (chart_service, aggregator, retention, frontend) that assume a
bare ticker string -- out of scope for "minimum safe change" this phase.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TickerIdentity:
    ticker: str
    exchange: str                      # canonical exchange chosen for this ticker
    is_dual_listed: bool = False       # True if an active row exists on the other exchange too
    shadowed_exchange: Optional[str] = None  # the OTHER exchange's row, if dual-listed


@dataclass(frozen=True)
class ShadowedListing:
    """An active stock_metadata row that will NOT be migrated under its own
    ticker string because a same-named listing on a higher-priority exchange
    already claims that string in `candles`. Reported, never silently lost."""
    ticker: str
    excluded_exchange: str
    canonical_exchange: str


EXCHANGE_PRIORITY = ["NSE", "BSE"]  # matches angelone_service.get_token's default of NSE


def resolve_ticker_universe(rows: List[Dict]) -> "tuple[List[TickerIdentity], List[ShadowedListing]]":
    """
    Pure function: given raw (ticker, exchange) rows for ACTIVE stock_metadata
    entries (caller does the DB read -- keeps this testable without a DB),
    return:
      - one TickerIdentity per distinct ticker string, using EXCHANGE_PRIORITY
        to pick the canonical exchange when a ticker is active on more than
        one exchange in this input,
      - a list of ShadowedListing records for every active row that lost that
        tie-break, so the collision is auditable rather than silently dropped.

    `rows`: list of {"ticker": str, "exchange": str} dicts (or objects with
    those attributes) -- already filtered to is_active=True by the caller.
    """
    by_ticker: Dict[str, List[str]] = {}
    for r in rows:
        ticker = r["ticker"] if isinstance(r, dict) else r.ticker
        exchange = r["exchange"] if isinstance(r, dict) else r.exchange
        if not ticker or not exchange:
            continue
        by_ticker.setdefault(ticker, []).append(exchange)

    identities: List[TickerIdentity] = []
    shadowed: List[ShadowedListing] = []

    for ticker, exchanges in sorted(by_ticker.items()):
        present = set(exchanges)
        canonical = None
        for ex in EXCHANGE_PRIORITY:
            if ex in present:
                canonical = ex
                break
        if canonical is None:
            # Active on an exchange outside our known priority list (e.g. a
            # future new segment) -- fall back to whatever is actually there
            # rather than silently excluding a real active security.
            canonical = sorted(present)[0]

        others = present - {canonical}
        identities.append(TickerIdentity(
            ticker=ticker,
            exchange=canonical,
            is_dual_listed=len(present) > 1,
            shadowed_exchange=sorted(others)[0] if others else None,
        ))
        for ex in sorted(others):
            shadowed.append(ShadowedListing(ticker=ticker, excluded_exchange=ex, canonical_exchange=canonical))

    return identities, shadowed


def resolve_ticker_universe_from_db(db) -> "tuple[List[TickerIdentity], List[ShadowedListing]]":
    """DB-backed convenience wrapper. Read-only SELECT only."""
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT ticker, exchange FROM stock_metadata WHERE is_active = TRUE"
    )).fetchall()
    return resolve_ticker_universe([{"ticker": r[0], "exchange": r[1]} for r in rows])
