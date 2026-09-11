#!/usr/bin/env python
"""Deployment migration gate for LEVERAGE.

DESIGN: explicit deployment step (Option B), not a container entrypoint
(Option A). See alembic/README's "Deployment sequence" section for the full
comparison this was chosen from. Short version: this is a single-instance,
single-worker (workers=1, gunicorn.conf.py) production deployment with no
rolling/blue-green replicas -- baking `alembic upgrade head` into the
container's own boot (CMD/entrypoint) would re-run it on EVERY container
restart, not just real deployments, turning a routine crash-recovery
restart into something that fails outright if the DB is briefly
unreachable. A separate, explicit step run BY THE DEPLOYER (CI/CD pipeline,
or an operator) before starting/restarting the app container gives a clear,
auditable migration checkpoint and keeps "deploy new code+schema" decoupled
from "the container process happened to restart".

Sequence this script enforces:

    alembic upgrade head
        -> verify the database actually reports being at the true head
           (computed from the migration files present in THIS checkout,
           never a hardcoded revision string)
        -> exit 0 (safe to start/restart the application)
           or exit 1 (STOP -- do not start/restart)

This script does NOT start, stop, or restart the application itself. The
caller is responsible for gating that on this script's exit code, e.g.:

    python backend/scripts/migrate_and_verify.py && \\
        docker compose up -d --no-deps app

Never invoke this against production without deliberate, explicit
authorization -- it takes no arguments and always targets whatever DB_*
environment variables are currently set, exactly like the `alembic` CLI
itself (which it invokes as a subprocess, reusing Alembic's own
already-configured, separate engine machinery in alembic/env.py -- this
script builds no engine or connection of its own).

Concurrency: because migrations run here, in a separate process, BEFORE any
Gunicorn worker exists (not from inside the running application), and this
app is hard-pinned to workers=1 with no multi-instance deployment, there is
no scenario where two workers or two instances race to migrate at once.
"""
import os
import re
import subprocess
import sys

from dotenv import load_dotenv

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Loads backend/.env when present (local/dev), and is a harmless no-op when
# it isn't (CI/deployment environments where DB_* vars are injected
# directly) -- override=False (the default) means an already-set real env
# var still wins, matching database.py's and alembic/env.py's own behavior.
# Without this, requirement #7 ("work where .env may not exist and
# credentials are injected through environment variables" AND the local/dev
# case where they come from .env) can't both hold: nothing else in this
# process loads .env before the check below runs.
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

# The minimum set of variables database.py/alembic/env.py need to build a
# connection URL. DB_PASSWORD is deliberately NOT required here -- both
# database.py and env.py already treat an empty/unset password as valid
# (falls back to a passwordless connection string), matching existing
# behavior; this script must not invent a NEW requirement that didn't
# already exist.
REQUIRED_ENV_VARS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER"]

_CREDENTIAL_PATTERN = re.compile(r"(postgresql://[^:\s]+):[^@\s]*@")


def _redact(text: str) -> str:
    """Defense in depth: strips a password out of any embedded
    postgresql://user:password@host URL before it's ever printed. Nothing
    in this script's normal path prints a raw connection string (Alembic's
    own CLI output, verified separately, never includes one either), but
    this guards against a future edit accidentally interpolating one (e.g.
    via a raw exception's str())."""
    return _CREDENTIAL_PATTERN.sub(r"\1:[REDACTED]@", text)


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    """Invokes the `alembic` CLI as a subprocess from backend/ -- exactly
    what a human operator or CI step would run by hand. This is the ONLY
    way this script ever touches the database: entirely through Alembic's
    own env.py machinery (its own separate engine, NullPool, already
    configured there) with no additional engine of this script's own."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )


def _extract_revision(stdout: str) -> str:
    """Pulls the leading hex revision hash off `alembic current`/`alembic
    heads` stdout, which looks like 'a45e15c6abca (head)' or 'a45e15c6abca'
    (or is empty, for a database with no alembic_version row at all)."""
    stdout = stdout.strip()
    if not stdout:
        return ""
    match = re.match(r"^([0-9a-f]+)", stdout.splitlines()[0].strip())
    return match.group(1) if match else ""


def main() -> int:
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        print(
            f"[migrate] FAILED: missing required environment variable(s): {', '.join(missing)}. "
            f"Refusing to guess a target database -- set them explicitly (a .env file is NOT "
            f"required; real deployment/CI environments inject these directly).",
            file=sys.stderr, flush=True,
        )
        return 1

    print("[migrate] Running: alembic upgrade head", flush=True)
    upgrade_result = _run_alembic("upgrade", "head")
    # Alembic's own INFO/WARNING logging goes to stderr; always surface it
    # (redacted) for operator visibility, success or failure.
    if upgrade_result.stderr:
        print(_redact(upgrade_result.stderr), file=sys.stderr, flush=True)

    if upgrade_result.returncode != 0:
        print(_redact(upgrade_result.stdout), file=sys.stderr, flush=True)
        print(
            "[migrate] FAILED: 'alembic upgrade head' exited with a non-zero status. "
            "Deployment must NOT proceed -- the application will not be started.",
            file=sys.stderr, flush=True,
        )
        return 1

    # Verify: the database's actual current revision(s) must equal the
    # true head(s) computed from the migration files present in THIS
    # checkout -- never a hardcoded constant, so this never needs manual
    # updating when a new migration is added later.
    current_result = _run_alembic("current")
    heads_result = _run_alembic("heads")
    if current_result.returncode != 0 or heads_result.returncode != 0:
        print(
            "[migrate] FAILED: could not verify the post-upgrade revision "
            "('alembic current'/'alembic heads' did not exit cleanly).",
            file=sys.stderr, flush=True,
        )
        return 1

    current_rev = _extract_revision(current_result.stdout)
    expected_rev = _extract_revision(heads_result.stdout)

    if not current_rev or not expected_rev or current_rev != expected_rev:
        print(
            f"[migrate] FAILED: post-upgrade verification mismatch -- database reports "
            f"current revision {current_rev!r}, expected head {expected_rev!r}. "
            f"Deployment must NOT proceed -- the application will not be started.",
            file=sys.stderr, flush=True,
        )
        return 1

    print(f"[migrate] OK -- database verified at head revision {current_rev}. "
          f"Safe to start/restart the application.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
