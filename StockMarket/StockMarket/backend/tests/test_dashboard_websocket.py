"""Phase 7 tests: dashboard WS connection bounding + slow-client cleanup.

Does NOT import main.py directly — main.py has expensive import-time side
effects (Angel One login, DB migrations, background threads; see the same
note in test_rate_limiting.py and test_news_ticker_cache.py). Instead this
mirrors main.py's ConnectionManager / _cleanup_dashboard_client logic
(main.py ~line 2956 onward) exactly, so the connection-limit, timeout, and
cleanup behavior is proven correct in isolation without paying that cost.
"""
import asyncio
import unittest
from typing import Dict, Optional


MAX_DASHBOARD_WS = 500
WS_SEND_TIMEOUT_SECONDS = 0.05  # shortened for fast tests; production value is 0.5
WS_MAX_CONSECUTIVE_TIMEOUTS = 3


class MockWebSocket:
    """Mirrors the subset of FastAPI's WebSocket used by ConnectionManager."""

    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.delay = delay  # simulated send latency
        self.fail = fail    # simulate a genuine send exception (not a timeout)
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message: dict):
        if self.fail:
            raise ConnectionError("simulated broken pipe")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append(message)

    async def close(self, code: int = 1000):
        self.closed = True
        self.close_code = code


class ConnectionManager:
    """Exact mirror of main.py's ConnectionManager after the Phase 7 changes."""

    def __init__(self, max_connections: int = MAX_DASHBOARD_WS):
        self.active_connections: Dict[str, MockWebSocket] = {}
        self.user_topics: Dict[str, set] = {}
        self._consecutive_timeouts: Dict[str, int] = {}
        self.max_connections = max_connections

    async def connect(self, websocket: MockWebSocket, client_id: str = None) -> Optional[str]:
        await websocket.accept()
        if client_id is None:
            client_id = str(id(websocket))
        if len(self.active_connections) >= self.max_connections:
            return None
        self.active_connections[client_id] = websocket
        self.user_topics[client_id] = set()
        self._consecutive_timeouts[client_id] = 0
        return client_id

    def disconnect(self, client_id: str):
        ws = self.active_connections.pop(client_id, None)
        if ws:
            try:
                asyncio.create_task(ws.close())
            except Exception:
                pass
        self.user_topics.pop(client_id, None)
        self._consecutive_timeouts.pop(client_id, None)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return

        async def _send(cid, ws):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=WS_SEND_TIMEOUT_SECONDS)
                self._consecutive_timeouts[cid] = 0
                return None
            except asyncio.TimeoutError:
                n = self._consecutive_timeouts.get(cid, 0) + 1
                self._consecutive_timeouts[cid] = n
                if n >= WS_MAX_CONSECUTIVE_TIMEOUTS:
                    return cid
                return None
            except Exception:
                return cid

        tasks = [_send(cid, ws) for cid, ws in self.active_connections.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        cleaned = []
        for cid in results:
            if isinstance(cid, str):
                self._cleanup(cid)
                cleaned.append(cid)
        return cleaned

    def _cleanup(self, client_id, viewed_mgr=None):
        topics = self.user_topics.get(client_id, set())
        if viewed_mgr and topics:
            viewed_mgr.unview_all(list(topics))
        self.disconnect(client_id)


class FakeViewedTickerManager:
    """Mirrors the view/unview_all interface used for cleanup verification."""

    def __init__(self):
        self.view_counts: Dict[str, int] = {}

    def view(self, ticker):
        self.view_counts[ticker] = self.view_counts.get(ticker, 0) + 1

    def unview(self, ticker):
        c = self.view_counts.get(ticker, 0)
        if c <= 1:
            self.view_counts.pop(ticker, None)
        else:
            self.view_counts[ticker] = c - 1

    def unview_all(self, tickers):
        for t in tickers:
            self.unview(t)


def run(coro):
    return asyncio.run(coro)


async def _sync_in_loop(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def call(fn, *args, **kwargs):
    """Run a sync function (like the real disconnect()/_cleanup()) inside a
    running event loop, matching every real call site in main.py -- both
    are only ever invoked from inside async handlers/broadcast(), where
    asyncio.create_task() inside disconnect() has a loop to schedule onto."""
    return run(_sync_in_loop(fn, *args, **kwargs))


class TestConnectionLifecycle(unittest.TestCase):
    def test_normal_connection(self):
        mgr = ConnectionManager()
        ws = MockWebSocket()
        cid = run(mgr.connect(ws))
        self.assertIsNotNone(cid)
        self.assertTrue(ws.accepted)

    def test_connection_registration(self):
        mgr = ConnectionManager()
        ws = MockWebSocket()
        cid = run(mgr.connect(ws))
        self.assertIn(cid, mgr.active_connections)
        self.assertEqual(len(mgr.active_connections), 1)

    def test_normal_disconnect(self):
        mgr = ConnectionManager()
        ws = MockWebSocket()
        cid = run(mgr.connect(ws))
        call(mgr.disconnect, cid)
        self.assertNotIn(cid, mgr.active_connections)
        self.assertNotIn(cid, mgr.user_topics)
        self.assertNotIn(cid, mgr._consecutive_timeouts)

    def test_reconnect(self):
        mgr = ConnectionManager()
        ws1 = MockWebSocket()
        cid1 = run(mgr.connect(ws1))
        call(mgr.disconnect, cid1)
        ws2 = MockWebSocket()
        cid2 = run(mgr.connect(ws2))
        self.assertIsNotNone(cid2)
        self.assertIn(cid2, mgr.active_connections)


class TestMaxConnectionLimit(unittest.TestCase):
    def test_limit_enforced(self):
        mgr = ConnectionManager(max_connections=5)
        accepted = []
        for i in range(5):
            cid = run(mgr.connect(MockWebSocket(), client_id=f"c{i}"))
            accepted.append(cid)
        self.assertTrue(all(c is not None for c in accepted))
        self.assertEqual(len(mgr.active_connections), 5)

    def test_over_limit_rejected(self):
        mgr = ConnectionManager(max_connections=5)
        for i in range(5):
            run(mgr.connect(MockWebSocket(), client_id=f"c{i}"))
        extra_ws = MockWebSocket()
        rejected = run(mgr.connect(extra_ws, client_id="overflow"))
        self.assertIsNone(rejected)
        # Rejected socket was still accept()ed (WS protocol requires a clean
        # close after handshake) but never registered.
        self.assertTrue(extra_ws.accepted)
        self.assertNotIn("overflow", mgr.active_connections)

    def test_existing_connections_unaffected_by_rejection(self):
        mgr = ConnectionManager(max_connections=5)
        for i in range(5):
            run(mgr.connect(MockWebSocket(), client_id=f"c{i}"))
        run(mgr.connect(MockWebSocket(), client_id="overflow"))
        self.assertEqual(len(mgr.active_connections), 5)
        for i in range(5):
            self.assertIn(f"c{i}", mgr.active_connections)

    def test_limit_recovery_after_disconnect(self):
        mgr = ConnectionManager(max_connections=5)
        for i in range(5):
            run(mgr.connect(MockWebSocket(), client_id=f"c{i}"))
        call(mgr.disconnect, "c0")
        new_cid = run(mgr.connect(MockWebSocket(), client_id="new"))
        self.assertIsNotNone(new_cid)
        self.assertEqual(len(mgr.active_connections), 5)

    def test_concurrent_connect_attempts_respect_limit(self):
        """Simulates N coroutines racing to connect when only room for a few remain."""
        mgr = ConnectionManager(max_connections=3)

        async def scenario():
            sockets = [MockWebSocket() for _ in range(10)]
            results = await asyncio.gather(
                *[mgr.connect(ws, client_id=f"race{i}") for i, ws in enumerate(sockets)]
            )
            return results

        results = run(scenario())
        accepted = [r for r in results if r is not None]
        self.assertEqual(len(accepted), 3)
        self.assertEqual(len(mgr.active_connections), 3)


class TestSlowClientProtection(unittest.TestCase):
    def test_single_slow_send_not_disconnected(self):
        mgr = ConnectionManager()
        slow_ws = MockWebSocket(delay=WS_SEND_TIMEOUT_SECONDS * 3)
        cid = run(mgr.connect(slow_ws, client_id="slow"))
        run(mgr.broadcast({"type": "tick"}))
        # One timeout is tolerated -- client stays connected.
        self.assertIn(cid, mgr.active_connections)
        self.assertEqual(mgr._consecutive_timeouts[cid], 1)

    def test_repeated_timeout_disconnects_client(self):
        mgr = ConnectionManager()
        slow_ws = MockWebSocket(delay=WS_SEND_TIMEOUT_SECONDS * 3)
        cid = run(mgr.connect(slow_ws, client_id="slow"))
        for _ in range(WS_MAX_CONSECUTIVE_TIMEOUTS):
            run(mgr.broadcast({"type": "tick"}))
        self.assertNotIn(cid, mgr.active_connections)

    def test_healthy_clients_unaffected_by_slow_client(self):
        mgr = ConnectionManager()
        healthy_a = MockWebSocket()
        healthy_b = MockWebSocket()
        slow = MockWebSocket(delay=WS_SEND_TIMEOUT_SECONDS * 3)
        healthy_d = MockWebSocket()
        run(mgr.connect(healthy_a, client_id="a"))
        run(mgr.connect(healthy_b, client_id="b"))
        run(mgr.connect(slow, client_id="c"))
        run(mgr.connect(healthy_d, client_id="d"))

        for _ in range(WS_MAX_CONSECUTIVE_TIMEOUTS):
            run(mgr.broadcast({"type": "tick"}))

        self.assertNotIn("c", mgr.active_connections)
        for cid in ("a", "b", "d"):
            self.assertIn(cid, mgr.active_connections)
        # Healthy clients received every broadcast.
        self.assertEqual(len(healthy_a.sent), WS_MAX_CONSECUTIVE_TIMEOUTS)
        self.assertEqual(len(healthy_b.sent), WS_MAX_CONSECUTIVE_TIMEOUTS)
        self.assertEqual(len(healthy_d.sent), WS_MAX_CONSECUTIVE_TIMEOUTS)

    def test_recovery_resets_consecutive_count(self):
        """A client that's slow once then fast again should NOT accumulate
        toward disconnection -- the counter resets on any successful send."""
        mgr = ConnectionManager()
        ws = MockWebSocket(delay=WS_SEND_TIMEOUT_SECONDS * 3)
        cid = run(mgr.connect(ws, client_id="flaky"))
        run(mgr.broadcast({"type": "tick"}))  # 1 timeout
        self.assertEqual(mgr._consecutive_timeouts[cid], 1)
        ws.delay = 0  # recovers
        run(mgr.broadcast({"type": "tick"}))  # success
        self.assertEqual(mgr._consecutive_timeouts[cid], 0)
        self.assertIn(cid, mgr.active_connections)

    def test_genuine_send_error_disconnects_immediately(self):
        """A real exception (e.g. broken pipe) should not wait for 3 strikes."""
        mgr = ConnectionManager()
        broken_ws = MockWebSocket(fail=True)
        cid = run(mgr.connect(broken_ws, client_id="broken"))
        run(mgr.broadcast({"type": "tick"}))
        self.assertNotIn(cid, mgr.active_connections)


class TestCleanupIdempotency(unittest.TestCase):
    def test_repeated_disconnect_no_exception(self):
        mgr = ConnectionManager()
        ws = MockWebSocket()
        cid = run(mgr.connect(ws))
        call(mgr.disconnect, cid)
        try:
            call(mgr.disconnect, cid)  # second call must not raise
            call(mgr.disconnect, cid)  # third call, still safe
        except Exception as e:
            self.fail(f"disconnect() raised on repeated call: {e}")
        self.assertNotIn(cid, mgr.active_connections)

    def test_cleanup_helper_idempotent_with_viewer_mgr(self):
        mgr = ConnectionManager()
        viewed_mgr = FakeViewedTickerManager()
        ws = MockWebSocket()
        cid = run(mgr.connect(ws))
        mgr.user_topics[cid] = {"RELIANCE", "TCS"}
        viewed_mgr.view("RELIANCE")
        viewed_mgr.view("TCS")

        call(mgr._cleanup, cid, viewed_mgr)
        self.assertEqual(viewed_mgr.view_counts, {})
        self.assertNotIn(cid, mgr.active_connections)

        # Calling cleanup again must not raise or double-decrement.
        try:
            call(mgr._cleanup, cid, viewed_mgr)
        except Exception as e:
            self.fail(f"cleanup raised on repeated call: {e}")
        self.assertEqual(viewed_mgr.view_counts, {})


class TestViewerSubscriptionIntegrity(unittest.TestCase):
    def test_connect_subscribe_disconnect_releases_viewer(self):
        mgr = ConnectionManager()
        viewed_mgr = FakeViewedTickerManager()
        ws = MockWebSocket()
        cid = run(mgr.connect(ws))

        mgr.user_topics[cid].add("RELIANCE")
        viewed_mgr.view("RELIANCE")
        self.assertEqual(viewed_mgr.view_counts["RELIANCE"], 1)

        call(mgr._cleanup, cid, viewed_mgr)
        self.assertNotIn("RELIANCE", viewed_mgr.view_counts)

    def test_repeated_reconnect_subscribe_no_cumulative_leak(self):
        mgr = ConnectionManager()
        viewed_mgr = FakeViewedTickerManager()

        for i in range(5):
            ws = MockWebSocket()
            cid = run(mgr.connect(ws, client_id=f"cycle{i}"))
            mgr.user_topics[cid].add("RELIANCE")
            viewed_mgr.view("RELIANCE")
            call(mgr._cleanup, cid, viewed_mgr)

        # After 5 full connect/subscribe/disconnect cycles, the view count
        # must be back to zero -- not accumulated to 5.
        self.assertNotIn("RELIANCE", viewed_mgr.view_counts)
        self.assertEqual(len(mgr.active_connections), 0)


class TestUnexpectedErrorsNotSwallowed(unittest.TestCase):
    def test_broadcast_send_exception_is_observable(self):
        """The broadcast error path must still log/report, not silently pass."""
        mgr = ConnectionManager()
        broken_ws = MockWebSocket(fail=True)
        run(mgr.connect(broken_ws, client_id="broken"))
        # broadcast() catches the exception to isolate it from other clients
        # (by design -- see class docstring), but it must return the cid so
        # the caller can act on it, not swallow it into nothing.
        cleaned = run(mgr.broadcast({"type": "tick"}))
        self.assertEqual(cleaned, ["broken"])


if __name__ == "__main__":
    unittest.main()
