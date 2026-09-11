# ADR-006: WebSocket Architecture

Status: Accepted

## Context

The frontend needs (a) broadcast market data (prices, movers, NIFTY) and
(b) private per-user messages (balance updates, order fills). The two have very
different cadence and authorization models.

Verified facts:
- **Public dashboard WS**: `/ws/dashboard` (main.py:2241). Clients subscribe via
  `{topics: [...]}`; a `ViewedTickerManager` (main.py:139) tracks per-ticker
  viewer counts and drives AngelOne WS subscribe/unsubscribe dynamically,
  with a 2000-subscription cap and LRU eviction of unviewed tickers
  (main.py:163-190).
- **Private user WS**: `/ws/user` (main.py:921). JWT-authenticated on connect
  (5s timeout), then bound to `user_id`. Managed by `UserConnectionManager`
  (`user_ws_manager`, websocket_manager.py:52) which supports multiple sockets
  per user (multiple tabs) and auto-disconnects failed sends
  (`send_personal_message`, websocket_manager.py:28-45).
- **Broadcast loops**: `_broadcast_dashboard` (dashboard data) and
  `_broadcast_angel_ticks` (main.py:3907) flush the AngelOne tick buffer to
  dashboard clients every ~100ms while ticking, 1s when idle, guarded by
  `_angel_tick_buffer_lock`.
- AngelOne ticks flow `angelone_service._handle_ws_tick` → `on_tick_callback` =
  `_on_angel_tick` (main.py:4214) → tick buffer + `candle_aggregator.process_tick`.

## Decision

Two independent WS surfaces:
1. **Public broadcast** (`/ws/dashboard`) — unauthenticated, topic-based,
   viewer-driven ticker subscription with a hard subscription cap and LRU
   eviction.
2. **Private user channel** (`/ws/user`) — JWT-authenticated, one namespace per
   `user_id`, used for `order_filled` and future balance/notification pushes.

## Alternatives considered

- **Single authenticated WS for everything**: rejected — market broadcast would
  require auth and would couple viewer-count tracking to logged-in state.
- **Server-Sent Events for broadcast**: rejected — bidirectional topic control
  (subscribe/unsubscribe per ticker) is cleaner over WS.
- **One connection per ticker**: rejected — too many sockets; topic-based
  multiplexing with viewer counts is more efficient.

## Consequences

- Viewer-driven subscription means **live data is only guaranteed for tickers
  currently viewed**. Non-viewed tickers (e.g., a position ticker no one has
  open in a browser) are evicted and have no WS ticks — the execution engine
  must not rely on WS subscription state (ADR-004).
- Multi-tab users get the same private message on every socket; failed sends are
  cleaned up.
- The tick buffer decouples AngelOne's fast tick thread from the event loop;
  both use the same `_on_angel_tick` callback to also feed the aggregator.

## Rollback considerations

Switching `/ws/dashboard` to authenticated mode would break the current
viewer-driven subscription design and the frontend clients. No reason to roll
back; the split is structural.

## Related files

- `backend/main.py` — `/ws/dashboard`, `/ws/user`, `_broadcast_*`, `_on_angel_tick`
- `backend/websocket_manager.py` — `UserConnectionManager`
- `backend/angelone_service.py` — tick feed, `subscribe_tickers`
- `docs/architecture/ADR-003` — view-driven recovery shares the same
  `ViewedTickerManager.view` hook
