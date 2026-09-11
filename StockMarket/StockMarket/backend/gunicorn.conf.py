"""Gunicorn config for LEVERAGE. Phase 11 of the production deployment sprint.

workers=1 is not a starting point to be tuned up later in this file -- it's
a hard requirement. AngelOne's WebSocket connection, PricePoller,
CandleAggregator, LiveTimeframeManager, the daily prefill scheduler,
CandleCache, latest_ticks, ViewedTickerManager, and the dashboard
WebSocket's ConnectionManager are all process-local, in-memory state with
no cross-process coordination (no Redis, no shared store). A second worker
would open a second AngelOne connection, run a second prefill, and hold a
second copy of every one of those, silently diverging from worker 1.
Multi-worker support is out of scope for this sprint and requires its own
architecture phase.
"""
import multiprocessing  # noqa: F401  (intentionally unused -- see workers= below)

bind = "0.0.0.0:8000"

# Hard-pinned, not derived from cpu_count() -- see module docstring.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"

# Generous request timeout: chart/indicator endpoints can do real work
# (candle aggregation, yfinance fallback) and the daily prefill runs in a
# background asyncio task on the same event loop, not a separate request,
# so this timeout governs individual HTTP requests, not startup.
timeout = 120
keepalive = 5
graceful_timeout = 30

# POST-LOAD MEMORY INVESTIGATION safety net: NOT a fix for the root cause
# (thread-per-sync-request arena growth -- addressed via MALLOC_ARENA_MAX in
# the Dockerfile and the AnyIO thread-limiter cap in main.py's startup()).
# This is a backstop in case some residual growth still creeps up over a
# very long uptime: gunicorn replaces the worker (spawns the new one FIRST,
# then gracefully drains and kills the old one -- no dropped connections,
# same pattern as a manual `docker compose up -d` recreate already proven
# safe in this app's startup/shutdown testing) after roughly 8000 requests,
# jittered +/-800 so the one worker doesn't recycle at a perfectly
# predictable moment. At this app's observed request rates this is on the
# order of hours between recycles, not minutes -- it should rarely if ever
# fire under normal load, but bounds the worst case instead of leaving it
# fully open-ended.
max_requests = 8000
max_requests_jitter = 800

# Nginx is the public entry point (Phase 9) -- gunicorn binds only to the
# container's internal interface and is never reached directly from the
# Internet.
accesslog = "-"   # stdout
errorlog = "-"    # stderr
loglevel = "info"

# Gunicorn's own worker-boot/exit hooks -- FastAPI's own @app.on_event
# startup/shutdown handlers (Phase 14's monitor probe, the daily prefill
# task, shutdown_flush, etc.) already own application lifecycle; nothing
# here duplicates them. These just confirm the process boundary in logs.


def on_starting(server):
    server.log.info("[Gunicorn] Starting LEVERAGE (workers=1, worker_class=%s)", worker_class)


def worker_exit(server, worker):
    server.log.info("[Gunicorn] Worker %s exiting", worker.pid)
