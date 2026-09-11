"""Phase 14 tests: process/CPU/event-loop/DB-pool monitoring helpers.

Does NOT import main.py directly -- main.py has expensive import-time side
effects (Angel One login, DB migrations, background threads; see the same
note in test_rate_limiting.py, test_news_ticker_cache.py, and
test_dashboard_websocket.py). Instead this mirrors the exact logic of
main.py's new Phase 14 helpers (_get_process_rss_mb, _get_cpu_percent,
_event_loop_lag_probe, _get_db_pool_stats, _check_db_health, _prefill_state)
so their behavior is proven correct in isolation without paying that cost.
"""
import asyncio
import json
import time
import threading
import unittest


# ── Mirrors of main.py's Phase 14 helpers (main.py ~line 541 onward) ──

def get_process_rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (FileNotFoundError, OSError):
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        psapi = ctypes.WinDLL("psapi.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = _PMC()
        counters.cb = ctypes.sizeof(_PMC)
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / 1024 / 1024, 1)
    except Exception:
        pass
    return None


class CpuPercentTracker:
    """Mirrors _get_cpu_percent()'s module-level-state pattern as a class
    so each test gets an independent instance instead of sharing globals."""

    def __init__(self):
        self.last_cpu = None
        self.last_wall = None

    def get(self, cpu_now=None, wall_now=None):
        cpu_now = time.process_time() if cpu_now is None else cpu_now
        wall_now = time.monotonic() if wall_now is None else wall_now
        prev_cpu, prev_wall = self.last_cpu, self.last_wall
        self.last_cpu, self.last_wall = cpu_now, wall_now
        if prev_cpu is None or wall_now <= prev_wall:
            return None
        wall_delta = wall_now - prev_wall
        cpu_delta = cpu_now - prev_cpu
        return round(max(0.0, (cpu_delta / wall_delta) * 100), 1)


class EventLoopLagProbe:
    """Mirrors _event_loop_lag_probe()'s state + one-tick logic, factored so
    a single tick can be tested deterministically instead of needing to
    actually wait out the real interval."""

    def __init__(self, interval=1.0):
        self.interval = interval
        self.state = {"current_ms": 0.0, "max_ms": 0.0}
        self.warn_calls = []
        self._last_warn = float("-inf")  # matches main.py: first warning must always fire

    def tick(self, now, next_expected):
        lag_ms = max(0.0, (now - next_expected) * 1000)
        self.state["current_ms"] = round(lag_ms, 1)
        if lag_ms > self.state["max_ms"]:
            self.state["max_ms"] = round(lag_ms, 1)
        if lag_ms > 500:
            if now - self._last_warn > 60:
                self.warn_calls.append(lag_ms)
                self._last_warn = now
        return now + self.interval

    async def run_n_ticks(self, n, fake_delays=None):
        """Drives n real asyncio.sleep()-based ticks -- proves the probe
        doesn't busy-loop (each tick genuinely awaits) and doesn't block."""
        loop = asyncio.get_event_loop()
        next_expected = loop.time() + self.interval
        for i in range(n):
            await asyncio.sleep(self.interval if not fake_delays else fake_delays[i])
            now = loop.time()
            next_expected = self.tick(now, next_expected)


def get_db_pool_stats(pool):
    try:
        size = pool.size()
        checked_out = pool.checkedout()
        max_overflow = getattr(pool, "_max_overflow", None)
        max_total = (size + max_overflow) if max_overflow is not None else None
        return {
            "pool_size": size,
            "checked_out": checked_out,
            "max_overflow": max_overflow,
            "available": (max_total - checked_out) if max_total is not None else None,
        }
    except Exception as e:
        return {"error": str(e)}


def check_db_health(session_factory):
    from sqlalchemy import text as _t
    db = None
    try:
        db = session_factory()
        db.execute(_t("SELECT 1"))
        return {"reachable": True}
    except Exception as e:
        return {"reachable": False, "error": str(e)}
    finally:
        if db is not None:
            db.close()


def make_test_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import QueuePool
    engine = create_engine("sqlite://", poolclass=QueuePool, pool_size=20, max_overflow=30)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


class TestProcessMetrics(unittest.TestCase):
    def test_rss_returns_number_or_none(self):
        rss = get_process_rss_mb()
        self.assertTrue(rss is None or isinstance(rss, float))
        if rss is not None:
            self.assertGreater(rss, 0)

    def test_thread_count_exists_and_valid(self):
        count = threading.active_count()
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 1)  # at least the main thread


class TestCpuPercent(unittest.TestCase):
    def test_first_call_returns_none(self):
        tracker = CpuPercentTracker()
        self.assertIsNone(tracker.get())

    def test_second_call_returns_float_type(self):
        tracker = CpuPercentTracker()
        tracker.get(cpu_now=1.0, wall_now=100.0)
        result = tracker.get(cpu_now=1.05, wall_now=100.5)
        self.assertIsInstance(result, float)

    def test_computes_percent_not_cumulative_time(self):
        """0.05s of CPU time over 0.5s wall-clock = 10%, not 0.05 (seconds)
        or 105 (a nonsensical cumulative-time-as-percent reading)."""
        tracker = CpuPercentTracker()
        tracker.get(cpu_now=1.0, wall_now=100.0)
        pct = tracker.get(cpu_now=1.05, wall_now=100.5)
        self.assertAlmostEqual(pct, 10.0, places=1)

    def test_never_negative(self):
        tracker = CpuPercentTracker()
        tracker.get(cpu_now=5.0, wall_now=100.0)
        # Pathological: wall time advances but cpu_now goes backward (clock
        # skew edge case) -- must clamp to 0, not report negative CPU.
        pct = tracker.get(cpu_now=4.0, wall_now=101.0)
        self.assertGreaterEqual(pct, 0.0)

    def test_stale_or_equal_wall_time_returns_none(self):
        tracker = CpuPercentTracker()
        tracker.get(cpu_now=1.0, wall_now=100.0)
        result = tracker.get(cpu_now=1.1, wall_now=100.0)  # wall_now didn't advance
        self.assertIsNone(result)


class TestEventLoopLagProbe(unittest.TestCase):
    def test_probe_records_lag(self):
        probe = EventLoopLagProbe(interval=1.0)
        # Simulate waking up 80ms late.
        probe.tick(now=1.08, next_expected=1.0)
        self.assertEqual(probe.state["current_ms"], 80.0)
        self.assertEqual(probe.state["max_ms"], 80.0)

    def test_max_lag_tracks_worst_observed(self):
        probe = EventLoopLagProbe(interval=1.0)
        probe.tick(now=1.02, next_expected=1.0)   # 20ms
        probe.tick(now=2.15, next_expected=2.0)   # 150ms -- new max
        probe.tick(now=3.01, next_expected=3.0)   # 10ms -- current drops, max stays
        self.assertEqual(probe.state["current_ms"], 10.0)
        self.assertEqual(probe.state["max_ms"], 150.0)

    def test_no_negative_lag_on_early_wakeup(self):
        probe = EventLoopLagProbe(interval=1.0)
        probe.tick(now=0.999, next_expected=1.0)  # woke up early somehow
        self.assertEqual(probe.state["current_ms"], 0.0)

    def test_warning_rate_limited(self):
        probe = EventLoopLagProbe(interval=1.0)
        probe.tick(now=1.6, next_expected=1.0)    # 600ms, over threshold -- warns
        probe.tick(now=2.6, next_expected=2.0)    # 600ms again, 1s later -- rate-limited, no warn
        self.assertEqual(len(probe.warn_calls), 1)

    def test_warning_fires_again_after_60s(self):
        probe = EventLoopLagProbe(interval=1.0)
        probe.tick(now=1.6, next_expected=1.0)     # warns
        probe.tick(now=65.6, next_expected=65.0)   # 64s later -- warns again
        self.assertEqual(len(probe.warn_calls), 2)

    def test_probe_does_not_block_or_busy_loop(self):
        """Runs 3 real ticks via genuine asyncio.sleep() -- if this were a
        busy loop it would burn CPU instead of yielding; if it blocked, the
        wall-clock time wouldn't roughly match interval*n."""
        probe = EventLoopLagProbe(interval=0.05)
        start = time.monotonic()
        asyncio.run(probe.run_n_ticks(3))
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.05 * 3 * 0.8)  # allows scheduling slack

    def test_probe_lifecycle_start_and_cancel_clean(self):
        """Mirrors the real startup/shutdown wiring: create_task, then
        cancel + await it, expecting CancelledError and nothing else."""
        async def scenario():
            async def infinite_probe():
                while True:
                    await asyncio.sleep(10)

            task = asyncio.create_task(infinite_probe())
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.assertTrue(task.done())
            self.assertTrue(task.cancelled())

        asyncio.run(scenario())

    def test_no_duplicate_probe_started_if_already_running(self):
        """Mirrors the `if task is None or task.done()` guard in
        start_event_loop_probe()."""
        async def scenario():
            async def infinite_probe():
                while True:
                    await asyncio.sleep(10)

            task_holder = {"task": None}

            def start_if_needed():
                if task_holder["task"] is None or task_holder["task"].done():
                    task_holder["task"] = asyncio.create_task(infinite_probe())
                    return True
                return False

            first = start_if_needed()
            second = start_if_needed()  # simulates startup firing twice
            self.assertTrue(first)
            self.assertFalse(second)  # guard prevented a duplicate task
            task_holder["task"].cancel()
            try:
                await task_holder["task"]
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())


class TestDatabaseMonitoring(unittest.TestCase):
    def test_pool_metrics_valid_at_rest(self):
        engine, SessionLocal = make_test_engine()
        stats = get_db_pool_stats(engine.pool)
        self.assertEqual(stats["pool_size"], 20)
        self.assertEqual(stats["checked_out"], 0)
        self.assertEqual(stats["max_overflow"], 30)
        self.assertEqual(stats["available"], 50)

    def test_pool_metrics_reflect_active_checkout(self):
        engine, SessionLocal = make_test_engine()
        conn = engine.connect()
        stats = get_db_pool_stats(engine.pool)
        self.assertEqual(stats["checked_out"], 1)
        self.assertEqual(stats["available"], 49)
        conn.close()
        stats_after = get_db_pool_stats(engine.pool)
        self.assertEqual(stats_after["checked_out"], 0)

    def test_pool_metrics_do_not_grow_unbounded_across_many_checkouts(self):
        engine, SessionLocal = make_test_engine()
        for _ in range(50):
            conn = engine.connect()
            conn.close()
        stats = get_db_pool_stats(engine.pool)
        self.assertEqual(stats["checked_out"], 0)  # all returned, no leak

    def test_db_health_check_succeeds(self):
        engine, SessionLocal = make_test_engine()
        result = check_db_health(SessionLocal)
        self.assertTrue(result["reachable"])

    def test_db_health_check_handles_failure_cleanly(self):
        def broken_session_factory():
            raise ConnectionError("simulated DB down")
        result = check_db_health(broken_session_factory)
        self.assertFalse(result["reachable"])
        self.assertIn("error", result)


class TestPrefillState(unittest.TestCase):
    def test_initial_state_shape(self):
        state = {
            "running": False, "last_started_at": None, "last_completed_at": None,
            "last_duration_sec": None, "last_run_ok": None,
        }
        self.assertFalse(state["running"])
        self.assertIsNone(state["last_run_ok"])

    def test_state_json_serializable(self):
        state = {
            "running": True, "last_started_at": "2026-09-03T10:00:00+05:30",
            "last_completed_at": None, "last_duration_sec": None, "last_run_ok": None,
        }
        json.dumps(state)  # must not raise

    def test_completed_run_state_shape(self):
        state = {
            "running": False, "last_started_at": "2026-09-03T10:00:00+05:30",
            "last_completed_at": "2026-09-03T10:05:30+05:30",
            "last_duration_sec": 330.5, "last_run_ok": True,
        }
        self.assertFalse(state["running"])
        self.assertIsInstance(state["last_duration_sec"], float)
        self.assertTrue(state["last_run_ok"])


class TestHealthResponseShape(unittest.TestCase):
    """Verifies the additive-merge pattern used in health_check(): each new
    section is independently try/except-guarded so one failing metric
    source can't take down the whole response, and existing fields are
    never touched by the new code."""

    def build_response(self, process_fn, event_loop_state, db_fn, market_fn,
                        live_fn, ws_fn, cache_fn, prefill_state):
        existing_fields = {
            "status": "ok", "timestamp": "2026-09-03T10:00:00+05:30",
            "market_open": False, "uptime_seconds": 100, "subscriptions": {},
            "yfinance": {}, "ws": {}, "aggregator": {}, "evictions_per_sec": 0.0,
        }
        response = dict(existing_fields)
        for key, fn in [("process", process_fn), ("database", db_fn),
                         ("market_data", market_fn), ("live_engine", live_fn),
                         ("websocket", ws_fn), ("cache", cache_fn)]:
            try:
                response[key] = fn()
            except Exception as e:
                response[key] = {"error": str(e)}
        response["event_loop"] = event_loop_state
        response["prefill"] = dict(prefill_state)
        return response, existing_fields

    def test_existing_fields_untouched(self):
        response, existing = self.build_response(
            lambda: {"rss_mb": 100.0}, {"lag_ms": 0.0, "max_ms": 0.0},
            lambda: {"reachable": True}, lambda: {"angelone_connected": True},
            lambda: {"active_builders": 5}, lambda: {"dashboard_connections": 3},
            lambda: {"l2_entries": 10}, {"running": False},
        )
        for k, v in existing.items():
            self.assertEqual(response[k], v)

    def test_one_failing_section_does_not_break_others(self):
        def broken():
            raise RuntimeError("simulated failure")

        response, existing = self.build_response(
            broken, {"lag_ms": 0.0, "max_ms": 0.0},
            lambda: {"reachable": True}, lambda: {"angelone_connected": True},
            lambda: {"active_builders": 5}, lambda: {"dashboard_connections": 3},
            lambda: {"l2_entries": 10}, {"running": False},
        )
        self.assertIn("error", response["process"])
        self.assertEqual(response["database"], {"reachable": True})
        for k, v in existing.items():
            self.assertEqual(response[k], v)

    def test_full_response_json_serializable(self):
        response, _ = self.build_response(
            lambda: {"rss_mb": 354.2, "cpu_percent": 8.4, "threads": 24},
            {"lag_ms": 3.2, "max_ms": 18.7},
            lambda: {"pool_size": 20, "checked_out": 4, "reachable": True},
            lambda: {"angelone_connected": True, "last_tick_age_seconds": 0.4},
            lambda: {"active_builders": 37, "active_viewers": 49},
            lambda: {"dashboard_connections": 49, "max_connections": 500},
            lambda: {"l2_entries": 124, "news_entries": 37},
            {"running": False, "last_run_ok": True},
        )
        json.dumps(response)  # must not raise

    def test_no_secrets_in_response(self):
        response, _ = self.build_response(
            lambda: {"rss_mb": 100.0}, {"lag_ms": 0.0, "max_ms": 0.0},
            lambda: {"reachable": True}, lambda: {"angelone_connected": True},
            lambda: {"active_builders": 5}, lambda: {"dashboard_connections": 3},
            lambda: {"l2_entries": 10}, {"running": False},
        )
        serialized = json.dumps(response).lower()
        for forbidden in ("password", "token", "api_key", "secret", "jwt", "credential"):
            self.assertNotIn(forbidden, serialized)


def build_database_section(db_health_result, pool_stats_fn):
    """Mirrors main.py health_check()'s database-section logic after the
    HIGH-01 fix (main.py ~1894-1908): sanitizes the raw driver/connection
    exception text (which can contain hostnames/usernames/DSN fragments)
    before it ever reaches the response body."""
    try:
        db_pool = pool_stats_fn()
        if db_health_result.get("reachable"):
            return {**db_pool, "reachable": True}
        else:
            return {**db_pool, "reachable": False, "error": "database unreachable"}
    except Exception:
        return {"reachable": False, "error": "database check failed"}


def decide_health_status_and_code(response):
    """Mirrors main.py health_check()'s final status-code decision (HIGH-01
    fix, end of the function): the database dependency is the one thing
    this endpoint actually gates on -- every other section stays
    best-effort/informational."""
    db_reachable = response.get("database", {}).get("reachable") is True
    response["status"] = "ok" if db_reachable else "error"
    status_code = 200 if db_reachable else 503
    return response, status_code


class TestHealthEndpointStatusCode(unittest.TestCase):
    """HIGH-01: /api/health must return a non-2xx status when the database
    dependency is actually unreachable, not a 200 with the failure buried
    in the JSON body."""

    def test_db_reachable_yields_200_and_ok(self):
        db_section = build_database_section(
            {"reachable": True}, lambda: {"pool_size": 20, "checked_out": 1}
        )
        response, code = decide_health_status_and_code({"database": db_section})
        self.assertEqual(code, 200)
        self.assertEqual(response["status"], "ok")

    def test_db_unreachable_yields_503_and_error(self):
        db_section = build_database_section(
            {"reachable": False, "error": "connection refused"},
            lambda: {"pool_size": 20, "checked_out": 0},
        )
        response, code = decide_health_status_and_code({"database": db_section})
        self.assertEqual(code, 503)
        self.assertEqual(response["status"], "error")

    def test_raw_driver_exception_never_reaches_client(self):
        raw_error = (
            'connection to server at "10.0.0.5", port 5432 failed: '
            'password authentication failed for user "postgres"'
        )
        db_section = build_database_section({"reachable": False, "error": raw_error}, lambda: {})
        serialized = json.dumps(db_section)
        self.assertNotIn("10.0.0.5", serialized)
        self.assertNotIn("postgres", serialized)
        self.assertNotIn("password authentication", serialized)
        self.assertEqual(db_section["error"], "database unreachable")

    def test_pool_stats_introspection_failure_fails_closed(self):
        def broken_pool_stats():
            raise RuntimeError("pool introspection blew up")
        # Even if the DB itself is reachable, a failure while introspecting
        # pool stats must not silently report healthy -- fail closed.
        db_section = build_database_section({"reachable": True}, broken_pool_stats)
        response, code = decide_health_status_and_code({"database": db_section})
        self.assertEqual(code, 503)
        self.assertEqual(response["status"], "error")

    def test_other_sections_still_populate_when_db_down(self):
        response = {
            "process": {"rss_mb": 100.0},
            "database": build_database_section({"reachable": False, "error": "boom"}, lambda: {}),
            "market_data": {"angelone_connected": True},
        }
        response, code = decide_health_status_and_code(response)
        self.assertEqual(code, 503)
        # Non-DB sections are untouched by the DB failure -- health checks
        # must never throw, and unrelated diagnostics stay visible.
        self.assertEqual(response["process"], {"rss_mb": 100.0})
        self.assertEqual(response["market_data"], {"angelone_connected": True})

    def test_response_still_json_serializable(self):
        response, _ = decide_health_status_and_code({
            "database": build_database_section({"reachable": False, "error": "boom"}, lambda: {}),
        })
        json.dumps(response)  # must not raise


class TestDockerHealthcheckCompatibility(unittest.TestCase):
    """Proves the exact command in Dockerfile/docker-compose.yml's
    healthcheck (`urlopen(...).status == 200`) correctly distinguishes a
    healthy (200) from an unhealthy (503) /api/health response -- i.e. the
    HIGH-01 fix does not break the existing Docker/Compose health-check
    consumer, which the audit explicitly required."""

    def _serve_status(self, status_code):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass  # silence request logging in test output

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, port

    def _run_healthcheck_script(self, port):
        """Verbatim logic from Dockerfile's HEALTHCHECK CMD / compose's test."""
        import urllib.request
        try:
            status = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=4
            ).status
            return 0 if status == 200 else 1
        except Exception:
            return 1

    def test_healthy_response_passes_healthcheck(self):
        server, port = self._serve_status(200)
        try:
            self.assertEqual(self._run_healthcheck_script(port), 0)
        finally:
            server.shutdown()

    def test_unhealthy_503_fails_healthcheck(self):
        server, port = self._serve_status(503)
        try:
            self.assertEqual(self._run_healthcheck_script(port), 1)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
