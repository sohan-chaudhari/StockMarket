# ADR-007: Retention Policy

Status: Accepted

## Context

The `candles` table accumulates intraday history (5m + resampled). Without a
lifecycle policy, storage grows unbounded and chart queries slow down. Older
fine-grained data should be progressively coarsened, not kept forever or deleted
outright.

Verified facts:
- `RetentionService` (retention_service.py:40) is a rolling lifecycle engine:
  atomic (BEGIN TX → convert → validate → COMMIT/ROLLBACK), idempotent
  (`ON CONFLICT DO NOTHING`), checksum-verified (count, volume sum, OHLC
  extremes) before commit, with checkpoint resume (`last_ticker` per job).
- Policy is data-driven from `config/retention_policy.yaml`: 5m→15m (44
  sessions), 15m→30m, 30m→1h, 1h→4h, 4h→1D, 1D→1W, 1W→1M, each with
  `after_days` / `after_trading_sessions` and `batch_size`.
- **Live window protection**: never converts data from the current trading day
  (retention_service.py:93-96).
- Scheduled at 02:00 IST via `_nightly_retention_cleanup` (main.py:4613, started
  at :4738). A second, older scheduler `_retention_scheduler_loop`
  (main.py:4947) is defined but **never started** (dead duplicate).
- `_nightly_retention_cleanup` also contains ~100 lines of legacy SQL
  (main.py:4636-4734) that are unreachable after a `continue` at :4634.

## Decision

- Centralize data lifecycle in `RetentionService`, driven by the YAML policy.
- Run once daily at 02:00 IST.
- Preserve the "never touch the current day" invariant so live charts are
  unaffected.

## Alternatives considered

- **Store everything forever**: rejected — unbounded storage, slow reads.
- **Hard-delete old intraday**: rejected — destroys fidelity; coarsening keeps
  usable history.
- **Multiple redundant schedulers**: rejected in practice — only
  `_nightly_retention_cleanup` is wired; the second scheduler is dead code that
  should be removed (audit finding H3).

## Consequences

- Data is progressively coarsened; premium-tier retention windows are tuned via
  the YAML (the legacy hardcoded 1m/5m/… windows in main.py are superseded).
- A checksum mismatch aborts a batch without corrupting data.
- Because the legacy block in `_nightly_retention_cleanup` is unreachable,
  edits near it are confusing; removal is low-risk cleanup.

## Rollback considerations

The policy YAML is the single knob — reverting a policy change is editing the
file. The scheduler can be disabled by removing the `create_task` call at
main.py:4738. Deleting the dead duplicate scheduler does not affect behavior.

## Related files

- `backend/retention_service.py` — `RetentionService`
- `backend/config/retention_policy.yaml` — policy
- `backend/config/timeframe_registry.py` — timeframe metadata
- `backend/main.py` — `_nightly_retention_cleanup` (:4613), dead
  `_retention_scheduler_loop` (:4947)
