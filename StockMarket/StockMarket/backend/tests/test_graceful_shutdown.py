"""Batch 2 (pre-AWS hardening) tests: production-grade graceful shutdown.

Does NOT import main.py directly -- main.py has expensive import-time side
effects (Angel One login, DB migrations, ~22 background tasks; see the same
note in test_monitoring.py, test_rate_limiting.py, test_dashboard_websocket.py).
Instead this mirrors the exact logic of main.py's shutdown machinery
(_track_task/_background_tasks, _close_all_client_websockets, and
shutdown_flush()'s ordering + idempotency guard) so the behavior is proven
correct in isolation without paying that cost.

Before Batch 2, shutdown_flush() only knew about ONE background task
(_event_loop_probe_task) out of ~22 spawned at startup, never closed client
WebSocket connections, and never disposed the SQLAlchemy engine -- every
restart was an uncoordinated teardown. These tests target exactly that gap.
"""
import asyncio
import unittest


# ── Mirrors of main.py's Batch 2 additions ──────────────────────────────

class BackgroundTaskTracker:
    """Mirrors main.py's module-level `_background_tasks` list + `_track_task()`
    (main.py, just above `async def startup()`)."""

    def __init__(self):
        self.tasks = []

    def track(self, task):
        self.tasks.append(task)
        return task


async def cancel_and_await_tasks(tasks, timeout):
    """Mirrors shutdown_flush()'s step 1 (main.py, Batch 2): cancel every
    not-yet-done tracked task, then await them all under one bounded
    timeout so a single stuck task can't hang shutdown indefinitely."""
    pending = [t for t in tasks if t is not None and not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        try:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
        except asyncio.TimeoutError:
            pass
    return pending


class FakeWebSocket:
    """Stand-in for a Starlette WebSocket's .close(code) coroutine."""

    def __init__(self, behavior="ok"):
        self.behavior = behavior  # "ok" | "raises" | "hangs"
        self.close_calls = 0
        self.closed_with_code = None

    async def close(self, code=1000):
        self.close_calls += 1
        if self.behavior == "raises":
            raise RuntimeError("socket already gone")
        if self.behavior == "hangs":
            await asyncio.sleep(999)
        self.closed_with_code = code


async def close_all_client_websockets(dashboard_connections, user_connections, per_socket_timeout=2):
    """Mirrors main.py's _close_all_client_websockets() (Batch 2): best-effort
    close (code 1001, 'going away') of every dashboard + per-user-tab
    WebSocket. One hung or erroring socket must never block closing the
    rest -- each close is independently try/except + timeout-guarded."""
    closed = 0
    for ws in list(dashboard_connections.values()):
        try:
            await asyncio.wait_for(ws.close(code=1001), timeout=per_socket_timeout)
            closed += 1
        except Exception:
            pass
    for conns in list(user_connections.values()):
        for ws in list(conns):
            try:
                await asyncio.wait_for(ws.close(code=1001), timeout=per_socket_timeout)
                closed += 1
            except Exception:
                pass
    return closed


class FakeShutdownOrchestrator:
    """Mirrors shutdown_flush()'s overall shape (main.py, Batch 2): step
    ordering (cancel tasks -> stop broker feed -> flush candles -> close
    client websockets -> dispose engine) plus the idempotency guard.
    Uses recorded call markers instead of the real AngelOne/candle_aggregator
    /database objects, since those require a live broker session / DB."""

    def __init__(self):
        self.shutdown_started = False
        self.call_order = []

    async def run(self, tasks, dashboard_connections, user_connections, task_timeout=1, socket_timeout=1):
        if self.shutdown_started:
            self.call_order.append("duplicate_ignored")
            return
        self.shutdown_started = True

        await cancel_and_await_tasks(tasks, timeout=task_timeout)
        self.call_order.append("tasks_cancelled")

        self.call_order.append("broker_feed_stopped")
        self.call_order.append("candles_flushed")

        await close_all_client_websockets(dashboard_connections, user_connections, socket_timeout)
        self.call_order.append("websockets_closed")

        self.call_order.append("engine_disposed")


# ── Tests ────────────────────────────────────────────────────────────────

class TestBackgroundTaskTracking(unittest.TestCase):
    def test_track_registers_task(self):
        tracker = BackgroundTaskTracker()

        async def scenario():
            async def noop():
                await asyncio.sleep(0)
            task = tracker.track(asyncio.create_task(noop()))
            await task
            self.assertIn(task, tracker.tasks)

        asyncio.run(scenario())

    def test_multiple_tasks_all_registered(self):
        tracker = BackgroundTaskTracker()

        async def scenario():
            async def loop_forever():
                while True:
                    await asyncio.sleep(10)

            for _ in range(5):
                tracker.track(asyncio.create_task(loop_forever()))
            self.assertEqual(len(tracker.tasks), 5)
            for t in tracker.tasks:
                t.cancel()
            await asyncio.gather(*tracker.tasks, return_exceptions=True)

        asyncio.run(scenario())


class TestCancelAndAwaitTasks(unittest.TestCase):
    def test_all_pending_tasks_cancelled_and_awaited(self):
        async def scenario():
            async def loop_forever():
                while True:
                    await asyncio.sleep(10)

            tasks = [asyncio.create_task(loop_forever()) for _ in range(3)]
            await asyncio.sleep(0.01)  # let them actually start
            pending = await cancel_and_await_tasks(tasks, timeout=1)
            self.assertEqual(len(pending), 3)
            for t in tasks:
                self.assertTrue(t.done())
                self.assertTrue(t.cancelled())

        asyncio.run(scenario())

    def test_already_done_tasks_are_left_untouched(self):
        async def scenario():
            async def finishes_immediately():
                return "done"

            task = asyncio.create_task(finishes_immediately())
            await task  # already done before shutdown runs
            pending = await cancel_and_await_tasks([task], timeout=1)
            self.assertEqual(pending, [])  # not re-touched
            self.assertFalse(task.cancelled())
            self.assertEqual(task.result(), "done")

        asyncio.run(scenario())

    def test_stubborn_task_bounded_by_timeout(self):
        """A pathological task that catches CancelledError and keeps doing
        (bounded) cleanup work must not make shutdown wait past the
        configured timeout -- proves the bound is real, not just typical-case
        luck."""
        async def scenario():
            async def slow_to_actually_stop():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.5)  # pretends to clean up
                    raise

            task = asyncio.create_task(slow_to_actually_stop())
            await asyncio.sleep(0.01)

            start = asyncio.get_event_loop().time()
            await cancel_and_await_tasks([task], timeout=0.1)
            elapsed = asyncio.get_event_loop().time() - start
            self.assertLess(elapsed, 0.4, "cancel_and_await_tasks must respect its timeout")

            # Let the task actually finish so it doesn't leak past this test.
            try:
                await asyncio.wait_for(task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        asyncio.run(scenario())

    def test_empty_task_list_returns_immediately(self):
        async def scenario():
            pending = await cancel_and_await_tasks([], timeout=1)
            self.assertEqual(pending, [])

        asyncio.run(scenario())


class TestCloseAllClientWebsockets(unittest.TestCase):
    def test_all_dashboard_connections_closed(self):
        async def scenario():
            dashboard = {"client1": FakeWebSocket(), "client2": FakeWebSocket()}
            closed = await close_all_client_websockets(dashboard, {})
            self.assertEqual(closed, 2)
            for ws in dashboard.values():
                self.assertEqual(ws.close_calls, 1)
                self.assertEqual(ws.closed_with_code, 1001)  # "going away"

        asyncio.run(scenario())

    def test_all_user_tabs_across_multiple_users_closed(self):
        async def scenario():
            user_conns = {
                101: [FakeWebSocket(), FakeWebSocket()],  # two tabs open
                202: [FakeWebSocket()],
            }
            closed = await close_all_client_websockets({}, user_conns)
            self.assertEqual(closed, 3)
            for conns in user_conns.values():
                for ws in conns:
                    self.assertEqual(ws.close_calls, 1)

        asyncio.run(scenario())

    def test_one_raising_socket_does_not_block_others(self):
        async def scenario():
            dashboard = {
                "bad": FakeWebSocket(behavior="raises"),
                "good1": FakeWebSocket(),
                "good2": FakeWebSocket(),
            }
            closed = await close_all_client_websockets(dashboard, {})
            self.assertEqual(closed, 2)  # only the 2 good ones counted
            self.assertEqual(dashboard["good1"].closed_with_code, 1001)
            self.assertEqual(dashboard["good2"].closed_with_code, 1001)

        asyncio.run(scenario())

    def test_one_hanging_socket_bounded_and_does_not_block_others(self):
        async def scenario():
            dashboard = {
                "stuck": FakeWebSocket(behavior="hangs"),
                "good": FakeWebSocket(),
            }
            start = asyncio.get_event_loop().time()
            closed = await close_all_client_websockets(dashboard, {}, per_socket_timeout=0.1)
            elapsed = asyncio.get_event_loop().time() - start
            self.assertEqual(closed, 1)  # only "good" counted
            self.assertLess(elapsed, 1.0, "a hung socket must not block the whole close pass")

        asyncio.run(scenario())

    def test_no_connections_is_a_noop(self):
        async def scenario():
            closed = await close_all_client_websockets({}, {})
            self.assertEqual(closed, 0)

        asyncio.run(scenario())


class TestShutdownOrchestrationOrder(unittest.TestCase):
    def test_steps_run_in_dependency_order(self):
        async def scenario():
            orch = FakeShutdownOrchestrator()

            async def quick():
                await asyncio.sleep(0.01)

            tasks = [asyncio.create_task(quick())]
            dashboard = {"c1": FakeWebSocket()}
            await orch.run(tasks, dashboard, {})
            self.assertEqual(orch.call_order, [
                "tasks_cancelled",
                "broker_feed_stopped",
                "candles_flushed",
                "websockets_closed",
                "engine_disposed",
            ])

        asyncio.run(scenario())

    def test_second_call_is_idempotent_noop(self):
        async def scenario():
            orch = FakeShutdownOrchestrator()
            await orch.run([], {}, {})
            first_order = list(orch.call_order)

            await orch.run([], {}, {})  # simulates a duplicate shutdown event
            self.assertEqual(orch.call_order, first_order + ["duplicate_ignored"])
            # Critically: engine_disposed/candles_flushed etc. do not appear twice.
            self.assertEqual(orch.call_order.count("engine_disposed"), 1)

        asyncio.run(scenario())

    def test_shutdown_with_active_connections_closes_all_of_them(self):
        """Combined scenario: background tasks running + multiple client
        connections open (dashboard and multi-tab user) when shutdown fires."""
        async def scenario():
            orch = FakeShutdownOrchestrator()

            async def loop_forever():
                while True:
                    await asyncio.sleep(10)

            tasks = [asyncio.create_task(loop_forever()) for _ in range(4)]
            dashboard = {f"c{i}": FakeWebSocket() for i in range(3)}
            user_conns = {1: [FakeWebSocket(), FakeWebSocket()], 2: [FakeWebSocket()]}

            await orch.run(tasks, dashboard, user_conns, task_timeout=1, socket_timeout=1)

            for t in tasks:
                self.assertTrue(t.done())
                self.assertTrue(t.cancelled())
            for ws in dashboard.values():
                self.assertEqual(ws.closed_with_code, 1001)
            for conns in user_conns.values():
                for ws in conns:
                    self.assertEqual(ws.closed_with_code, 1001)
            self.assertEqual(orch.call_order[0], "tasks_cancelled")
            self.assertEqual(orch.call_order[-1], "engine_disposed")

        asyncio.run(scenario())


# ── SMOKE-01 regression tests ───────────────────────────────────────────
# Found by a real production smoke test (Docker/Gunicorn, live AngelOne
# login, real SIGTERM) that the mirrored unit tests above couldn't catch:
# these bugs only manifest against the REAL SmartWebSocketV2 class / a real
# asyncio event loop under uvicorn, not against a plain MagicMock or a
# standalone asyncio.run() scenario.

async def stop_angelone_feed(sws, logout_fn):
    """Mirrors shutdown_flush()'s AngelOne-teardown step after the SMOKE-01
    fix (main.py): closing the WS socket and logging out are independent
    try/except blocks, so a failure closing the socket can no longer skip
    logout() entirely (which is exactly what happened before the fix --
    SmartWebSocketV2 has no .close() method, so the AttributeError it threw
    was caught by a single shared try/except that logout() was also inside,
    and logout() silently never ran)."""
    close_error = None
    logout_error = None
    try:
        if sws is not None:
            sws.close_connection()
    except Exception as e:
        close_error = e
    try:
        logout_fn()
    except Exception as e:
        logout_error = e
    return close_error, logout_error


class TestAngelOneShutdownStepsAreIndependent(unittest.TestCase):
    def test_logout_still_runs_when_socket_close_fails(self):
        class BrokenSocket:
            def close_connection(self):
                raise AttributeError("'SmartWebSocketV2' object has no attribute 'close'")

        logout_calls = []

        async def scenario():
            return await stop_angelone_feed(BrokenSocket(), lambda: logout_calls.append(1))

        close_error, logout_error = asyncio.run(scenario())
        self.assertIsInstance(close_error, AttributeError)
        self.assertIsNone(logout_error)
        self.assertEqual(logout_calls, [1], "logout() must still run even if closing the socket failed")

    def test_both_succeed_when_healthy(self):
        class HealthySocket:
            def __init__(self):
                self.closed = False

            def close_connection(self):
                self.closed = True

        sock = HealthySocket()
        logout_calls = []

        async def scenario():
            return await stop_angelone_feed(sock, lambda: logout_calls.append(1))

        close_error, logout_error = asyncio.run(scenario())
        self.assertIsNone(close_error)
        self.assertIsNone(logout_error)
        self.assertTrue(sock.closed)
        self.assertEqual(logout_calls, [1])

    def test_no_socket_skips_close_but_still_logs_out(self):
        logout_calls = []

        async def scenario():
            return await stop_angelone_feed(None, lambda: logout_calls.append(1))

        close_error, logout_error = asyncio.run(scenario())
        self.assertIsNone(close_error)
        self.assertIsNone(logout_error)
        self.assertEqual(logout_calls, [1])

    def test_logout_failure_reported_independently_of_close_outcome(self):
        class HealthySocket:
            def close_connection(self):
                pass

        def broken_logout():
            raise RuntimeError("session already invalid")

        async def scenario():
            return await stop_angelone_feed(HealthySocket(), broken_logout)

        close_error, logout_error = asyncio.run(scenario())
        self.assertIsNone(close_error)
        self.assertIsInstance(logout_error, RuntimeError)


def make_tracked_close_task(close_coro):
    """Mirrors ConnectionManager.disconnect()'s fixed fire-and-forget close
    (SMOKE-01 fix, main.py): attaches a done-callback that retrieves (and
    records) the task's exception. Before the fix, a failing close() left
    the task's exception unretrieved -- asyncio logs an unhandled "Task
    exception was never retrieved" error (with a full traceback) once the
    task is garbage collected, exactly what the real smoke test observed
    during shutdown."""
    logged = []

    def _log_close_error(t):
        try:
            exc = t.exception()
            if exc:
                logged.append(exc)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(close_coro())
    task.add_done_callback(_log_close_error)
    return task, logged


class TestDashboardDisconnectCloseIsRetrieved(unittest.TestCase):
    def test_failing_close_is_logged_not_left_unretrieved(self):
        async def scenario():
            async def failing_close():
                raise RuntimeError("Unexpected ASGI message 'websocket.close', after sending 'websocket.close' or response already completed.")

            task, logged = make_tracked_close_task(failing_close)
            await asyncio.sleep(0)  # let the task run to completion
            try:
                await task
            except RuntimeError:
                pass  # the caller doesn't need to handle it -- the callback already retrieved it
            return logged

        logged = asyncio.run(scenario())
        self.assertEqual(len(logged), 1)
        self.assertIsInstance(logged[0], RuntimeError)

    def test_successful_close_logs_nothing(self):
        async def scenario():
            async def ok_close():
                return None

            task, logged = make_tracked_close_task(ok_close)
            await task
            return logged

        logged = asyncio.run(scenario())
        self.assertEqual(logged, [])

    def test_task_exception_is_actually_retrieved(self):
        """The core of the fix: calling task.exception() inside the
        done-callback is what marks the exception retrieved (asyncio's
        unhandled-exception warning fires from the task's __del__ only if
        .exception()/.result() was never called) -- confirm the callback
        genuinely calls it rather than just swallowing silently elsewhere."""
        async def scenario():
            async def failing_close():
                raise ValueError("boom")

            task, logged = make_tracked_close_task(failing_close)
            await asyncio.sleep(0)
            self.assertTrue(task.done())
            # If the callback already retrieved it, calling .exception()
            # again here is safe and returns the same exception (asyncio
            # does not raise "already retrieved" errors -- it's idempotent).
            self.assertIsInstance(task.exception(), ValueError)
            return logged

        logged = asyncio.run(scenario())
        self.assertEqual(len(logged), 1)


if __name__ == "__main__":
    unittest.main()
