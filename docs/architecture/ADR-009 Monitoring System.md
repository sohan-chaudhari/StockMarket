# ADR-009: Monitoring System

Status: Accepted

## Context

The live candle system has many cooperating components (aggregator, recovery,
timeframe manager, cache, retention, event bus, calendar). Failures are often
silent (e.g., a stale tick feed, a stuck recovery, an empty cache). Operations
needs a single health surface.

Verified facts:
- `Monitor` (monitor.py:25) is a registry of named health callables, exposed at
  `GET /api/monitoring/candle-system` (main.py:4916). It reports uptime, an
  overall healthy/degraded verdict, and per-component dicts; any component
  returning `status == "unhealthy"` (or a falsy result) flips the overall state.
- Components registered in `init_recovery_service` (main.py:4905-4911):
  aggregator, recovery, timeframe_manager, cache, retention, event_bus,
  exchange_calendar.
- Dedicated health helpers: `get_aggregator_health`, `get_recovery_health`,
  `get_timeframe_manager_health`, `get_cache_health`, `get_retention_health`,
  `get_event_bus_health` (monitor.py:57+).
- Additional operational signals exist outside the Monitor: `_monitoring`
  counters (ticks processed, WS reconnects, replay counts — main.py:422-435,
  4270-4271, 4283), `/api/candle-health`, `/api/aggregator-stats`, and a
  5-minute `print_health` loop (main.py:4318-4326).
- A daily aggregator health monitor runs every 300s; Option A removed the
  `backpressure_events` key from `get_stats` and `print_health`.

## Decision

Centralized health registry (`Monitor`) exposed as one REST endpoint, plus
supplementary counters/endpoints for low-level operational data. All candle
subsystems must register a health callable that returns a status dict.

## Alternatives considered

- **Prometheus / external metrics export**: rejected for now — no infrastructure;
  a lightweight JSON endpoint matches the current deployment.
- **Per-component log-only health**: rejected — no aggregated "is the system
  degraded?" answer.

## Consequences

- Adding a component requires registering a health fn (a deliberate hook that
  keeps coverage complete).
- Overall verdict is "healthy" only if *all* components report healthy — a
  strict AND, which surfaces partial failures loudly.
- The strict AND can be noisy (one stale ticker degrades the whole signal);
  `_monitoring` counters provide the finer-grained context.

## Rollback considerations

Monitoring is read-only; removing a registered component just removes its row
from the endpoint. No behavioral rollback needed.

## Related files

- `backend/monitor.py` — `Monitor` + health helpers
- `backend/main.py` — `/api/monitoring/candle-system`, registration
  (main.py:4905-4911), `_monitoring` counters, `/api/candle-health`,
  `/api/aggregator-stats`
