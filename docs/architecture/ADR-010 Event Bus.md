# ADR-010: Event Bus

Status: Accepted

## Context

The aggregator produces live candle events; the live timeframe manager and
recovery service consume them. These components run on different threads
(AngelOne WS thread, aggregator flush thread, asyncio event loop), so a
thread-safe publish/subscribe mechanism is needed to decouple producers from
consumers.

Verified facts:
- `EventBus` (event_bus.py:7) supports sync and async handlers, both prefixed
  with the namespace `v1.` (e.g., subscribing to `candle.5m.updated` registers
  for `v1.candle.5m.updated`). Async handlers are dispatched onto the event loop
  via `asyncio.run_coroutine_threadsafe` when a loop is set (event_bus.py:47-52).
- The loop is bound in `wire_event_bus_and_recovery` (main.py:4967-4970).
- **Current producers**: `candle.5m.updated` (aggregator.py:286),
  `candle.5m.completed` (aggregator.py:333), `recovery.complete`
  (recovery_service.py:96).
- **Current consumers**: `live_timeframe_manager` subscribes to all three
  (live_timeframe_manager.py:249-251).
- **No trade/order events exist** (verified by grep): the entire order lifecycle
  (ADR-004) is invisible to the bus.

## Decision

Use a single in-process, thread-safe `EventBus` (namespace `v1.`) as the
decoupling layer between candle producers and higher-timeframe consumers. Sync
handlers run inline; async handlers are marshaled onto the event loop.

## Alternatives considered

- **Direct calls between components**: rejected — the aggregator would need to
  know about every consumer (timeframe manager, charts, future features).
- **External message broker (Redis/RabbitMQ)**: rejected — overkill for a
  single-process app; no infra dependency wanted.
- **Per-feature ad-hoc callbacks**: rejected — no uniform subscription model or
  introspection (the Monitor reports subscriber count, event_bus.py:54).

## Consequences

- Adding a new candle consumer is a one-line subscription, decoupled from the
  aggregator.
- Async handlers require the event loop to be set at startup; emitting before
  `wire_event_bus_and_recovery` runs simply drops async handler dispatch
  (sync handlers still run).
- **Gap (planned)**: order lifecycle events (`order.placed`, `order.filled`)
  should be published so notifications, auditing, and the execution engine are
  decoupled from the WS manager (recommendation from the TP/SL flow analysis).

## Rollback considerations

The bus is additive — handlers can be unsubscribed via `off`, or the loop
binding removed to disable async dispatch. No migration required.

## Related files

- `backend/event_bus.py` — `EventBus`, `event_bus` singleton
- `backend/aggregator.py` — `candle.5m.updated/completed` producers
- `backend/recovery_service.py` — `recovery.complete` producer
- `backend/live_timeframe_manager.py` — subscribers
- `backend/main.py` — `wire_event_bus_and_recovery`
