# Architecture Decision Records

Living documentation of significant architectural decisions for the StockMarket
platform. Each record captures *why* the system is built the way it is, so future
development (and future AI sessions) can reason about the design without
re-deriving it.

## How to use this record

- **Create a new ADR** whenever a significant decision is made (new subsystem,
  changed data flow, security boundary, scaling choice).
- **Update an existing ADR** when the decision changes in practice — keep the
  record truthful, not aspirational. Add a `## Updates` section rather than
  rewriting history.
- **Status**: `Accepted` / `Superseded` / `Deprecated`. Add `Superseded by: ADR-XXX`
  when replaced.

## Current decisions

| ID | Title | Status |
|----|-------|--------|
| ADR-001 | Backend Source of Truth | Accepted |
| ADR-002 | Live Candle Architecture | Accepted |
| ADR-003 | Recovery Service | Accepted |
| ADR-004 | Order Execution Engine | Accepted (NOT wired — see record) |
| ADR-005 | Authentication | Accepted |
| ADR-006 | WebSocket Architecture | Accepted |
| ADR-007 | Retention Policy | Accepted |
| ADR-008 | Chart Data Pipeline | Accepted |
| ADR-009 | Monitoring System | Accepted |
| ADR-010 | Event Bus | Accepted |
| ADR-011 | Execution-Pinned Subscriptions | Accepted (not implemented) |

## Template

Every ADR includes: **Context**, **Decision**, **Alternatives considered**,
**Consequences**, **Rollback considerations**, **Related files**.
