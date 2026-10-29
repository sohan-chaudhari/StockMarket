"""A chart open must never wait on a provider.

Measured 2026-10-29 20:10 IST, production:

    GET /api/stock-data/range?ticker=JPL&range=ALL      -> no response, http=000, 90 s
    GET /api/stock-data/intraday/paginated?ticker=JPL   -> 200 in 48 ms

Cause: `_stock_data_range_core` called
`historical_service.get_historical_candles(ONE_DAY)` **inside the request**, and that
call blocks on the shared historical limiter for up to `_RATE_LIMIT_BACKOFF_SEC`
(90 s) whenever a cooldown is active. A young listing such as JPL (two sessions) hits
it on EVERY open, because its row count is permanently below `MIN_DAILY_RECORDS` --
which is exactly the reported "I open JPL, wait a minute, and the graph never opens".

Same rule for yfinance: an 8 s download is only worth doing when the newest day is
actually missing, not when the ticker merely has a short history.
"""

import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(
    encoding="utf-8", errors="replace")


def _function_body(name: str) -> str:
    i = SRC.index("def %s(" % name)
    j = SRC.index("\ndef ", i + 10)
    return SRC[i:j]


def _code_only(text: str) -> str:
    """Drop comment lines so a comment mentioning a call does not look like the call."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_the_daily_endpoint_never_calls_the_provider_inline():
    body = _code_only(_function_body("_stock_data_range_core"))
    assert "get_historical_candles" not in body, (
        "ONE_DAY blocks up to 90 s behind the shared limiter; it must never run in a "
        "chart request")
    assert "_spawn_daily_provider_refresh(clean_ticker)" in body


def test_the_depth_only_shortfall_is_not_fetched_inline():
    body = _function_body("_stock_data_range_core")
    assert "if _newest_missing and not _is_yfinance_failed(" in body, (
        "a young listing's depth shortfall must not cost an inline yfinance download")
    assert "_depth_short = len(records) < MIN_DAILY_RECORDS" in body


def test_the_daily_fill_is_backgrounded_and_capped():
    body = _function_body("_spawn_daily_provider_refresh")
    assert "daemon=True" in body
    assert "_DAILY_ONDEMAND_MAX_THREADS" in body
    assert "_daily_ondemand_inflight" in body


def test_the_intraday_repair_is_backgrounded():
    """_sync_repair_recent_bg must be spawned on a daemon thread, not called inline."""
    i = SRC.index("target=_sync_repair_recent_bg")
    window = SRC[max(0, i - 400):i + 400]
    assert "threading.Thread(" in window or "Thread(" in window
    assert "daemon=True" in window


def test_a_depth_shortfall_becomes_a_job_not_a_request():
    body = _function_body("_stock_data_range_core")
    assert '"1D"' in body and 'reason="chart_open"' in body


def test_weekly_and_monthly_gap_fills_are_time_bounded():
    """Every yfinance wait in a request path must be short: waiting 20 s for the shared
    semaphore and then 15 s for a 'max' download turned a 1W open into a 35 s hang."""
    for name in ("_resample_gap_fill",):
        body = _code_only(_function_body(name))
        assert "timeout=20" not in body, "no 20 s semaphore wait in a chart request"
        assert "timeout=15)" not in body, "no 15 s download in a chart request"
        assert body.count("_yf_semaphore.acquire(blocking=True, timeout=1.0)") == 2
        assert body.count("timeout=5)") == 2


def test_weekly_and_monthly_only_fetch_inline_when_the_tail_is_missing():
    """A young listing can never satisfy `len < 150`, so gating on depth alone ran a
    'max' download on EVERY 1W open (measured 20 s on JPL right after a restart)."""
    body = _code_only(_function_body("_resample_gap_fill"))
    assert "_weekly_tail_gap" in body and "_monthly_tail_gap" in body
    assert "if _weekly_tail_gap and not _too_young_for_inline and not _is_yfinance_failed(" in body
    assert "if _monthly_tail_gap and not _too_young_for_inline_m and not _is_yfinance_failed(" in body
    # a young listing's 1W/1M chart is its 1D series resampled -- no inline download
    assert "_too_young_for_inline = _daily_rows < 60" in body


def test_enqueue_reuses_an_active_job_instead_of_adding_another():
    """Measured 2026-10-29: JPL held 7 rows for 5m because only FINISHED jobs were
    debounced, so every open inserted a new row for a slightly different range."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "backfill_jobs.py").read_text(
        encoding="utf-8", errors="replace")
    i = src.index("def enqueue(")
    body = _code_only(src[i:src.index("\ndef ", i + 10)])
    assert "SELECT id, status FROM backfill_jobs" in body
    assert "OR status IN ('queued','running','retry_wait','partially_complete')" in body
    assert "if reason in ('chart_open', 'history_page') and _recent[1] in (" in body


def test_the_depth_guard_is_computed_once_before_both_branches():
    """Regression: computed inside the 1W branch only, it raised NameError on 1M
    (JPL returned HTTP 500). It must be defined before both branches."""
    body = _function_body("_resample_gap_fill")
    w = body.rindex('if target_tf == "1W":')     # the fill branch (an earlier check exists)
    m = body.rindex('if target_tf == "1M":')
    d = body.index("_daily_rows = db.query(func.count(")
    assert d < w < m, (d, w, m)
    assert "_too_young_for_inline_m = _too_young_for_inline" in body


def test_the_ondemand_fill_is_a_thread_not_a_background_task():
    """Starlette awaits background_tasks before the ASGI call completes, so a chart
    that used them still waited for the whole provider fetch (TATACAP 15m: 18.5 s)."""
    body = _code_only(_function_body("_spawn_ondemand_fill"))
    assert "daemon=True" in body and "perform_on_demand_backfill(" in body
    route = _code_only(_function_body("get_intraday_paginated"))
    assert "background_tasks.add_task(" not in route, (
        "background_tasks delays the response; use _spawn_ondemand_fill")
    assert "_spawn_ondemand_fill(clean_ticker, interval, fill_start, now)" in route


def test_background_provider_work_is_bounded():
    """Each provider waiter holds a DB session, so an unbounded burst empties the pool
    and then EVERY request waits pool_timeout (measured: /api/health timed out at 15 s
    while 7 backends sat 'idle in transaction')."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "historical_service.py").read_text(
        encoding="utf-8", errors="replace")
    assert "BACKGROUND_PROVIDER_SLOTS = threading.BoundedSemaphore(" in src
    assert 'os.getenv("BG_PROVIDER_SLOTS", "2")' in src

    body = _code_only(_function_body("_sync_repair_recent_bg"))
    assert "BACKGROUND_PROVIDER_SLOTS.acquire(blocking=False)" in body, (
        "a 5m repair must skip rather than queue behind the provider")
    assert "BACKGROUND_PROVIDER_SLOTS.release()" in body

    dpr = (pathlib.Path(__file__).resolve().parents[1] / "daily_provider_refresh.py").read_text(
        encoding="utf-8", errors="replace")
    assert "BACKGROUND_PROVIDER_SLOTS.acquire(timeout=0.5)" in dpr
    assert 'stats["reason"] = "provider_busy"' in dpr


def test_the_inline_reconcile_is_time_bounded():
    """One open in thirty took 18.3 s because the UPSERT waited on the aggregator's
    flush transaction. A chart request must give up and serve stored rows instead."""
    assert "_reconcile_daily_from_5m(2, _session_coverage_min_bars(), ticker=ticker," in SRC
    assert "timeout_ms=1500" in SRC
    body = _code_only(_function_body("_reconcile_daily_from_5m"))
    assert "SET LOCAL lock_timeout" in body and "SET LOCAL statement_timeout" in body


def test_no_endpoint_inlines_a_historical_call_in_a_request():
    """Belt and braces: no chart endpoint may call the provider directly."""
    for name in ("_stock_data_range_core", "get_intraday_paginated",
                 "_intraday_paginated_route"):
        body = _code_only(_function_body(name))
        assert "get_historical_candles" not in body, name
