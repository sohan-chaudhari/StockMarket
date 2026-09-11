# ADR-005: Authentication

Status: Accepted

## Context

The platform has authenticated users, paper-trading accounts, and private
WebSocket channels. Tokens must be unforgeable, sessions must be revocable
per-user, and the system must fail closed if configuration is missing.

Verified facts:
- JWT via `python-jose` (`jose`), HS256, 24h expiry (auth.py:29-31).
- `JWT_SECRET_KEY` is read from env; **if missing, falls back to a hardcoded
  development key** `"temporary_dev_secret_key_change_me_immediately"`
  (auth.py:24-28). The `.env` currently sets a real 64-char key, so the fallback
  is dormant but dangerous on a misconfigured deploy.
- Passwords are hashed with `bcrypt`; `validate_password` enforces 12+ chars
  with upper/lower/digit/special (auth.py:48-63).
- `get_current_user` (auth.py:590) and `get_current_user_optional` are the
  FastAPI dependency guard used by route handlers; `HTTPBearer` extracts the
  token from `Authorization: Bearer`.
- Auth logs to `logs/auth.log` via `RotatingFileHandler`.
- The private WS `/ws/user` (main.py:921) authenticates once on connect (5s
  timeout) via the JWT, then binds the socket to `user_id`.

## Decision

- HS256 JWT with 24h expiry; secret sourced from `JWT_SECRET_KEY` env var.
- Passwords stored as bcrypt hashes; strict password policy.
- **Planned (approved intent, awaiting implementation): fail fast** — if
  `JWT_SECRET_KEY` is absent, refuse to start with a clear error instead of
  falling back to the development key. This is Task 2 in the audit roadmap.

## Alternatives considered

- **Fixed built-in secret**: rejected — anyone reading the source could forge
  tokens.
- **Session cookies / server-side sessions**: rejected — stateless JWT is
  simpler and already used across WS + REST + multiple frontend pages.
- **Silently empty secret**: rejected — would make HS256 use an empty key.

## Consequences

- Tokens survive restarts (stateless) — logout is client-side only; account
  deactivation is enforced by checking `user.is_active` on each request/WS
  connect.
- The dev-key fallback means a misconfigured production deploy is *currently*
  exploitable (token forgery). This is the security reason the fail-fast change
  is prioritized.
- Private WS re-authenticates per connection, so token revocation is
  connection-bounded.

## Rollback considerations

The fail-fast change is safe to adopt: the `.env` already contains a valid key,
so startup behavior is unchanged in the working deployment. Reverting means
restoring the fallback (not recommended).

## Related files

- `backend/auth.py` — token creation/verification, deps, password policy
- `backend/routers/auth_router.py` — registration/login/logout endpoints
- `backend/main.py` — `/ws/user` auth handshake
- `backend/models.py::User` — `is_active`, `virtual_balance`
