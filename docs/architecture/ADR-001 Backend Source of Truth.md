# ADR-001: Backend Source of Truth

Status: Accepted

## Context

The platform runs a live FastAPI backend that must serve real-time market data,
charts, and paper trading. Multiple Python modules (aggregator, chart service,
recovery, retention, execution) read and write price/order state concurrently.
We need a single authority for "what is the current price / current candle
state" and for "what is the user's position/balance" to avoid divergence.

Key facts:
- PostgreSQL is the only durable store (`database.py`), accessed via SQLAlchemy
  `SessionLocal` / `get_db` (pool_size=10, max_overflow=20, `pool_pre_ping=True`).
- The live 5m candle builder (`aggregator.py::Live5mBuilder`) holds the
  authoritative in-memory forming candles; the DB `candles` table holds the
  durable completed 5m history.
- Trading state (`users`, `positions`, `orders`, `transactions`) is
  authoritative only in PostgreSQL, protected with `with_for_update()` row locks
  in `trade_service.py`.

## Decision

Two-tier source of truth, by domain:

1. **Real-time price/candle state**: the in-memory `candle_aggregator`
   (and its `latest_ticks` feed from AngelOne) is authoritative for the *current*
   forming candle and current price. The DB is the durable record of *completed*
   candles only.
2. **Trading/account state**: PostgreSQL is the sole source of truth. All balance
   changes, position opens/closes, and order status transitions happen inside a
   single transaction with `SELECT ... FOR UPDATE` on the affected `User`/
   `Position` rows.

Every component must pick one authority per datum and never write both tiers
for the same field.

## Alternatives considered

- **DB as the only source of truth for live prices**: rejected. 1m/5m candle
  flush cadence would add seconds of latency and high write volume for a field
  that only needs in-memory freshness.
- **In-memory as source of truth for orders**: rejected. Would lose positions on
  restart with no audit trail.

## Consequences

- Forming candles are volatile — must be reconstructed after restart
  (see ADR-003 Recovery Service).
- Order execution must not use DB candles as the live price source
  (see ADR-004 — current engine price feed is incorrect).
- Row locking is mandatory in `trade_service.py`; any new trading code must use
  `with_for_update()` on the same rows to stay race-safe.

## Rollback considerations

None needed — this is an invariant, not a feature. Violating it (e.g., reading
orders from memory or writing live prices to the DB at tick cadence) is a
regression, not a configuration toggle.

## Related files

- `backend/database.py` — engine, session factory, `get_ist_now()`
- `backend/aggregator.py` — in-memory forming candle authority
- `backend/trade_service.py` — transactional trading authority
- `backend/models.py` — `Position`, `Order`, `Transaction`, `Candle`
