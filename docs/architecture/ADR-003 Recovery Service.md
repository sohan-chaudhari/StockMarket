# ADR-003: Recovery Service

Status: Accepted

## Context

After a process restart (or an AngelOne WS gap), the in-memory forming 5m
candles are gone. Rebuilding all ~5,500 tickers eagerly at startup is slow and
hits external data sources unnecessarily. The system needs a way to reconstruct
a ticker's current-day candle state on demand.

Verified facts:
- `RecoveryService` (recovery_service.py:24) takes `db_session_factory`,
  `historical_service`, and `yf_downloader`; exposes `needs_recovery(ticker)`
  (recovery_service.py:35) and `recover_ticker(ticker, live_5m_builder)`
  (recovery_service.py:52).
- It is wired as a lazy, per-ticker mechanism: `init_recovery_service()` is
  called at startup (main.py:4154), and `ViewedTickerManager.view` is patched
  (main.py:4975-4984) so the **first time a user views a ticker**, if recovery
  is needed, a background thread runs `recover_ticker` before/while the forming
  candle is used.
- On recovery completion it emits `recovery.complete` on the event bus
  (recovery_service.py:96); `live_timeframe_manager` subscribes to it
  (live_timeframe_manager.py:251).
- The WS watchdog replays up to 100 stale tickers on reconnect
  (main.py:4273-4287) via the recovery service.

## Decision

Use **lazy, viewer-driven recovery**: recover only tickers that are actually
being viewed, on first view after startup or after a WS gap. No bulk recovery
at boot. The aggregator also seeds a forming candle from the last DB candle on
first tick (`_init_ticker`, aggregator.py:359-364).

## Alternatives considered

- **Eager full recovery at startup**: rejected. 5,500 tickers × external fetch
  is too slow and wasteful; most tickers are never viewed.
- **No recovery / empty state until first real tick**: rejected. Charts would
  show gaps and forming-candle continuity would break for mid-session restarts.

## Consequences

- Recovery cost is paid only when a ticker is actually needed — good scaling.
- There is a small window on first view where the forming candle is
  reconstructed from history, so the very first snapshot may lag the true
  current tick by the fetch duration.
- Because recovery is tied to *viewership*, any component that needs a ticker's
  live state but is not a viewer (e.g., the order execution engine) must trigger
  recovery itself — the engine currently does not, which compounds ADR-004.

## Rollback considerations

Disabling recovery simply means forming candles start empty after restart and
recover from the first real tick. No data loss; only chart continuity is
affected. No rollback path needed beyond removing the view-patch in
`wire_event_bus_and_recovery`.

## Related files

- `backend/recovery_service.py` — `RecoveryService`
- `backend/main.py` — `init_recovery_service`, patched `view`, WS watchdog replay
- `backend/live_timeframe_manager.py` — `_on_recovery_complete`
- `backend/aggregator.py` — `_init_ticker`, `recover_from_db`
