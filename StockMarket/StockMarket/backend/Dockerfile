# syntax=docker/dockerfile:1
# LEVERAGE production image.
#
# Build context is the PARENT of this file (StockMarket/StockMarket/StockMarket/),
# not backend/ itself -- main.py's BASE_DIR climbs two directories up from its
# own location (os.path.dirname(os.path.dirname(__file__))) and mounts the
# frontend as a StaticFiles directory from BASE_DIR/frontend. backend/ and
# frontend/ must stay siblings inside the image or that mount fails at
# startup. Build with:
#   docker build -f backend/Dockerfile -t leverage-backend ..\..\..
# (from this directory) or see docker-compose.yml, which sets this up already.
#
# Python 3.13.7 matches what's actually installed in the project's venv --
# verified via `python --version`, not assumed.
FROM python:3.13-slim

# psycopg2-binary ships prebuilt wheels for this platform, so no compiler
# toolchain (gcc/build-essential) is needed. libpq5 is still required at
# runtime because psycopg2-binary links against it dynamically.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Mirrors the working dev layout: backend/ and frontend/ as siblings under
# a common root, with PYTHONPATH+cwd pointed at backend/ -- the same
# arrangement start_all.py already uses successfully outside Docker.
WORKDIR /app/backend
ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # POST-LOAD MEMORY INVESTIGATION: every synchronous (`def`, not `async
    # def`) FastAPI route -- /api/stock-data/chart, /intraday, /api/all-stocks
    # among the busiest -- runs on a worker thread from AnyIO's thread pool
    # (Starlette's own run_in_threadpool), not the main event-loop thread,
    # because they use SQLAlchemy's sync Session (Depends(get_db)) and can't
    # simply be made async without a separate async-DB-driver migration --
    # out of scope for this phase. glibc's default allocator gives each
    # thread its own malloc arena (up to 8x the core count on 64-bit Linux)
    # and does not return that memory to the OS even once the thread goes
    # idle or exits, which a live comparison during this investigation
    # confirmed: tracemalloc's own view of live Python objects grew ~94MB
    # across an idle->API->WS->mixed-load sequence while process RSS grew
    # ~400MB in that same sequence -- most of the growth was structurally
    # invisible to Python's own allocator tracking, correlating tightly with
    # thread-count spikes instead. MALLOC_ARENA_MAX caps the number of
    # arenas glibc will create, forcing thread reuse of a small, bounded set
    # instead of growing one per concurrent thread -- standard, well-
    # documented production tuning for exactly this pattern (e.g. Instagram
    # Engineering's well-known writeup), zero code/behavior change, fully
    # reversible by removing this line.
    MALLOC_ARENA_MAX=2

# Dependencies first for Docker layer caching -- only reinstalled when
# requirements.txt actually changes, not on every application code edit.
COPY backend/requirements.txt .
# `playwright` was removed from requirements.txt (DEPENDENCY AUDIT
# REMEDIATION phase) -- it was listed but never imported anywhere in this
# app's own code (inherited from the sibling News_Sentiment app's
# requirements), and its wheel alone bundles a ~138MB Node.js driver
# binary that showed up as an entirely separate, unused attack surface in
# a container vulnerability scan. requirements-dev.txt (pytest/
# pytest-asyncio) is deliberately NOT copied or installed here either --
# this image ships production dependencies only.
# Cache mount (not baked into any image layer, purely a build-time
# accelerator): pip's download cache survives between builds even when
# requirements.txt changes, since only the *new* line needs a fresh
# download. Final image is unaffected either way.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY backend/ .
COPY frontend/ ../frontend/

# backend/logs/ is excluded by .dockerignore (months of accumulated
# auth.log rotation files + assorted dated subdirectories shouldn't ship
# in a fresh image), but auth.py's RotatingFileHandler("logs/auth.log", ...)
# needs the directory to already exist -- it won't create it. Recreated
# empty here, chowned to appuser below.
RUN mkdir -p logs

# Non-root runtime user, with a real home directory: a plain `useradd -r`
# (no -m) leaves HOME pointed at a directory that doesn't exist, and
# sentry_sdk.init() (main.py:14) tries to write session/cache state under
# $HOME at import time -- without one, gunicorn's worker fails with
# "Permission denied: '/home/appuser'" before the app ever gets a chance
# to start. -m creates it; it's chowned along with the rest of /app below.
RUN groupadd -r appuser && useradd -r -m -g appuser -d /home/appuser appuser \
    && chown -R appuser:appuser /app /home/appuser
USER appuser
ENV HOME=/home/appuser

EXPOSE 8000

# Uses the existing /api/health endpoint (Phase 14) -- deliberately a plain
# stdlib urllib call, not curl, so no extra package has to be installed
# just for the healthcheck. start_period is generous because LEVERAGE's
# startup does real work (AngelOne login, ~5.5k-row stock metadata load,
# the daily prefill firing 30s in) before it's meaningfully "ready" --
# Docker must not flag the container unhealthy just because that's still
# running.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# Exec form (JSON array): runs gunicorn as PID 1 directly, no intermediate
# shell, so SIGTERM from `docker stop` reaches gunicorn's own signal
# handler instead of being swallowed by a shell process.
CMD ["gunicorn", "main:app", "-c", "gunicorn.conf.py"]
