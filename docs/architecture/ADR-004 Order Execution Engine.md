# ADR-004: Order Execution Engine

Status: Accepted — class implemented, **NOT wired into startup**

## Context

Users place paper trades with TP (take profit) and SL (stop loss) trigger
prices. `TradingService.open_position` / `set_limits` create `orders` rows with
`order_type="TP"/"SL"`, `trigger_price`, and `status="PENDING"`
(trade_service.py:100-120, 272-305). For those triggers to ever fire, a
background engine must monitor prices, decide when a trigger is hit, and close
the position. Today the engine exists but is never started.

Verified facts:
- `PriceMonitorService` (execution_engine.py:15) with `start()` (2s poll loop),
  `check_pending_orders()`, `execute_order()`, `recover_stuck_orders()`; global
  `price_monitor` (execution_engine.py:254) and `start_execution_engine()`
  (execution_engine.py:257).
- **Zero callers**: `execution_engine` is not imported by any runtime module;
  `start_execution_engine()` never invoked; not referenced in the startup
  handler (main.py:3931-4859). Verified by grep + import smoke test.
- **The price feed is broken**: `get_current_prices()` queries
  `Candle WHERE timeframe=='1m' AND is_completed AND date==today`
  (execution_engine.py:99-107). No runtime module ever writes `1m` rows to the
  `candles` table (aggregator writes only 5m — ADR-002). The query always
  returns empty, so it falls back to `StockData` daily close — yesterday's
  price, not live. Also uses `date.today()` (system-local) not IST.
- Execution is **paper only**: `TradingService.close_position` (trade_service.py:147)
  credits `virtual_balance`; there is no broker order-send for TP/SL.
- Good safety properties already present: `with_for_update()` on the Position
  row, `status=="OPEN"` guard, and an `EXECUTING` intermediate status make
  double-execution impossible across processes.

## Decision

- **Keep the engine's state machine** (row lock + OPEN guard + EXECUTING→EXECUTED
  two-phase commit) as the design for trigger evaluation and closing.
- **Do not wire it into startup until the price source is fixed.** Wiring the
  current code would evaluate triggers against stale daily closes.
- **Required fix before wiring**: replace the DB `1m` price query with the live
  in-memory feed, in priority order:
  1. `angelone_service.latest_ticks` (sub-second, guarded by `latest_ticks_lock`);
  2. `candle_aggregator.get_current(ticker)` (forming 5m close);
  3. last-resort `Candle` 5m completed, with IST-aware "today".
- **Fix `recover_stuck_orders`**: if the position is already CLOSED, reconcile
  the EXECUTING order to EXECUTED/CANCELLED instead of re-evaluating it.

## Alternatives considered

- **Wire as-is now**: rejected. Broken price feed (1m table empty) would produce
  spurious or never-firing triggers; worse than not running.
- **Event-driven (subscribe to tick stream) instead of polling**: preferred
  long-term but not required for first correctness fix; polling the in-memory
  feed at 2s is acceptable for paper trading and much simpler.

## Consequences

- Until wired, **all TP/SL orders stay PENDING forever** — the highest-severity
  functional gap in the platform (reported in the Stability Audit as C1).
- Once wired correctly, position close, balance credit, and order status
  transition remain atomic inside `close_position`'s transaction.
- `recover_stuck_orders` runs on every `start()`; with multi-worker deploys each
  worker would run its own loop — DB locks prevent duplicate closes but polling
  is duplicated (acceptable).

## Rollback considerations

The engine can be safely disabled by simply not starting it (current state) —
orders remain PENDING and nothing is executed. Any future wiring can be reverted
the same way. No DB migration involved.

## Related files

- `backend/execution_engine.py` — `PriceMonitorService`
- `backend/trade_service.py` — `close_position`, `set_limits`, `open_position`
- `backend/main.py` — startup handler (where wiring belongs), `/ws/user`
- `backend/websocket_manager.py` — `order_filled` notification path
- `docs/architecture/ADR-002` (why 1m candles don't exist)
