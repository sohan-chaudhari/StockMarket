# ADR-011: Execution-Pinned WebSocket Subscriptions

Status: Accepted — architecture agreed, **not implemented**

## Context

Live prices are delivered to the execution engine only through the AngelOne WS
tick stream (`angelone_service.latest_ticks`), and that stream's subscription
set is organized around **viewers**, not around order safety. A ticker with an
active TP/SL order that nobody has open in a browser loses its WS subscription
within ~5 minutes (viewer debounce) or sooner (LRU eviction under the 2000-cap),
so `latest_ticks` goes stale and the engine's price source degrades to completed
5m candles or the previous daily close. This is the exact failure mode flagged in
ADR-004 (C1): TP/SL must not depend on whether a human is watching the chart.

Verified facts (this record's scope — subscription lifecycle only):
- Viewer interest is refcounted in `_view_counts`; subscribe happens on 0→1
  (main.py:160-161); unsubscribed after 5-minute debounce via `_process_pending`
  (main.py:220-236); LRU eviction runs for unviewed tickers under the 2000 cap
  (main.py:163-190).
- Actual subscribe/unsubscribe is delegated to `angelone_service.subscribe_tickers`
  / `unsubscribe_tickers` (angelone_service.py:351-386), which resend the full
  token list over one WS message; `on_open` resubscribes every `subscribed_tokens`
  on reconnect (angelone_service.py:157-158).
- `latest_ticks` is a dict keyed by ticker, updated by the WS handler
  (`_handle_ws_tick`) and by REST fallback pollers over the dashboard/mover set
  (angelone_service.py:290-303, price_poller.py). Execution-order tickers outside
  that set have no REST fallback today.
- The engine's `check_pending_orders()` **already loads every PENDING (and, after
  recovery, EXECUTING) order every cycle** (execution_engine.py:54-57) — this is the
  free, restart-survivable source of "which tickers must stay fresh".

## Decision

Unify subscription ownership under a **single interest model with two reference
counts**: ticker stays WS-subscribed **iff** `viewer_refs[t] + exec_refs[t] >= 1`.

- **Viewer referent** — existing behavior unchanged (refcount, debounce, eviction).
- **Execution referent (refcount, not a set)** — `execution_ref_count[ticker]`,
  owned by the execution engine. Multiple active orders can exist for the same
  ticker, so a set-based pin would under-count and prematurely drop a still-needed
  subscription.
  - **Increment** when a qualifying order (PENDING, or EXECUTING after recovery)
    appears for that ticker.
  - **Decrement** when such an order is closed (executed, cancelled, or otherwise
    no longer qualifying).
  - The subscription must remain active until the **last** active order for the
    ticker is completed — i.e., until `execution_ref_count` returns to 0.
  - The count is (re)derived from DB order state at startup/recovery so it
    survives restart, using the cycle's own order query (no extra DB work).

Rules:
- **Unsubscribe only when** `viewer_ref_count[ticker] == 0` **AND**
  `execution_ref_count[ticker] == 0` (and the viewer debounce elapses).
- Execution-referenced tickers are always subscribed (via the existing
  `subscribe_tickers` path), never debounce-unsubscribed, never LRU-evicted.
- The engine reconciles the execution refcount each `check_pending_orders` cycle
  (idempotent: increment newly-seen tickers, decrement finished ones); the
  startup `recover_stuck_orders` path counts EXECUTING tickers before any
  trigger evaluation.
- WS reconnect re-subscribes execution-referenced tickers automatically (they
  remain in `subscribed_tokens`); the audit below heals any drift.
- **Subscription consistency audit (60s)**: a lightweight periodic task compares
  the expected interest map (`execution_ref_count + viewer_ref_count` per ticker)
  against `angelone_service.subscribed_tokens` and automatically repairs any
  mismatch (subscribing tickers with interest > 0, unsubscribing tickers with
  interest 0). Guards against drift from reconnects, partial subscribe/unsubscribe
  calls, or missed decrements.
- No global "subscribe to all 5500" change. Execution-referenced tickers are
  bounded by actual open orders (normally a handful), well under the existing
  subscription cap.

## Alternatives considered

- **Subscribe to the full universe at startup and never evict**: rejected — near-universe
  subscription is already fragile today (startup breadth is not a durability
  guarantee for the execution path) and does not scale with brokers' rate limits.
- **Execution set (single pin per ticker)**: rejected — with multiple active
  orders per ticker a set loses the "until the last order completes" guarantee.
  A refcount is the correct primitive.
- **REST-poll execution tickers exclusively**: rejected — REST 2s polling for every
  order ticker duplicates the fresh WS feed and strains rate limits; WS is
  primary. Optional REST backstop for execution tickers may be added to the poller later.
- **Event-driven (subscribe on trigger fire) instead of reconcile**: rejected —
  reactive-only misses triggers between events; per-cycle reconciliation is
  simpler and proven (ADR-004 already polls).

## Consequences

- TP/SL evaluation keeps a fresh price for any open-order ticker regardless of
  browser state — the property that makes ADR-004 wiring safe.
- The subscription authority becomes one refcount model shared by viewers and
  orders (`viewer_ref_count` + `execution_ref_count`); internal only, no WS
  schema change.
- Eviction can never kill an open order's price (a ticker with `execution_ref_count
  > 0` is never evicted); conversely, no permanent global subscription is
  introduced.
- The 60s audit bounds drift cost: any subscription mismatch self-heals within
  one minute without coupling the engine's correctness to the viewer machinery.

## Implementation guarantees

Approved during pre-implementation verification. These are binding constraints
on how ADR-011 is coded.

1. **Execution reference counts are DB-authoritative.** The execution manager
   never performs mutable `++`/`--` operations. Each reconciliation cycle
   recomputes the expected execution reference counts directly from the database
   (`orders WHERE status IN ('PENDING','EXECUTING')`, grouped per ticker) and
   reconciles against the current in-memory state. Counts are assigned from DB
   truth, never mutated at point-of-truth — this makes negative counts,
   duplicate increments, and duplicate decrements structurally impossible, and
   guarantees identical rebuild after restart.

2. **Protect `subscribed_tokens` with a dedicated lock.** All reads and writes,
   including reconnect logic (`on_open` re-send), subscribe, unsubscribe, and
   audits, must use the same lock. Without it, the WS thread and API threads
   race on the token set (verified: `subscribed_tokens` is a plain `set`
   today — angelone_service.py:73 — mutated from API threads while `on_open`
   reads it from the WS thread at :157-158).

3. **The subscription audit is subscribe-only.** It may repair missing
   subscriptions immediately, but it must never directly unsubscribe. All
   unsubscriptions continue through the existing debounce mechanism
   (`_pending_unsub`, 5-minute `_process_pending` path), which re-checks both
   reference counts before release. This prevents the audit from dropping a
   ticker whose interest changed between the audit's read and its action.

4. **Never hold internal locks while performing broker subscribe/unsubscribe
   calls.** Compute the desired changes under lock, release the lock, then make
   the broker API calls (`angelone_service.subscribe_tickers` /
   `unsubscribe_tickers`). Lock ordering is therefore always
   `internal lock → (release) → broker call`; the broker call never runs while
   holding a lock. Preserves the verified no-deadlock property between
   `latest_ticks_lock` and the reference-count locks.

5. **Invariant — expected subscription set.** At all times the expected
   subscription set is

   ```
   { ticker | viewer_ref_count > 0 OR execution_ref_count > 0 }
   ```

   Every reconciliation and every audit must preserve this invariant: any ticker
   matching the predicate is subscribed and never evicted/unsubscribed; any
   ticker not matching it is eligible for debounced unsubscribe only.

## Rollback considerations

Pure additive: until the execution refcounts are wired, behavior is today's
(viewer-driven only). A bug in the refcount can be reverted by simply not calling
the increment/decrement APIs; no DB migration, no WS route change. The execution
refcount and the 60s audit can be disabled independently of the engine
price-source fix (Phase 1 of the plan).

## Related files

- `backend/main.py` — `ViewedTickerManager` (refcount eviction/debounce guards),
  periodic subscription audit task
- `backend/execution_engine.py` — `check_pending_orders`, `recover_stuck_orders`
- `backend/angelone_service.py` — `latest_ticks`, `subscribe_tickers`/`unsubscribe_tickers`
- `docs/architecture/ADR-006` — base WS/viewer design this refactors
- `docs/architecture/ADR-004` — the engine this keeps fed