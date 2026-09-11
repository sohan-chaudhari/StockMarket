"""Batch 3 (pre-AWS hardening) tests: /ws/user heartbeat / dead-connection
detection.

Does NOT import main.py directly (heavy import-time side effects -- same
note as test_monitoring.py, test_graceful_shutdown.py, test_rate_limiting.py).
Instead this mirrors user_websocket_endpoint's Batch 3 concurrent
receive+heartbeat logic (main.py, /ws/user) against a fake WebSocket, the
same style test_user_websocket.py already uses for websocket_manager.py.

Before Batch 3, /ws/user had NO heartbeat at all: a laptop sleep or network
drop without a clean TCP close left receive_json() blocked forever, letting
a zombie session sit registered against MAX_USER_WS_PER_USER (6) until the
OS's own (long, unconfigured) TCP timeout eventually fired.
"""
import asyncio
import time
import unittest


class WebSocketDisconnectStub(Exception):
    """Stand-in for fastapi.WebSocketDisconnect -- avoids importing FastAPI
    just for this exception type in a mirror test."""


class FakeUserWebSocket:
    """Stand-in for the Starlette WebSocket used by /ws/user's receive and
    heartbeat loops.

    - `incoming`: messages returned by receive_json(), in order.
    - `stay_open`: once `incoming` is exhausted, if True, receive_json()
      blocks on an asyncio.Event (as a real socket does when the client is
      alive but quiet) instead of immediately raising a disconnect.
    - `simulate_external_close()`: mimics a real socket closing out from
      under a pending receive_json() call (e.g. a server-initiated
      ws.close() during Batch 2's shutdown) -- wakes it immediately,
      unlike just flipping a flag a sleeping coroutine won't re-check.
    - `send_behavior`: "ok" | "raises" | "hangs" -- controls send_json().
    """

    def __init__(self, incoming=None, stay_open=True, send_behavior="ok"):
        self.incoming = list(incoming or [])
        self.stay_open = stay_open
        self.send_behavior = send_behavior
        self.sent = []
        self._closed_event = asyncio.Event()
        self._message_event = asyncio.Event()
        if self.incoming:
            self._message_event.set()

    def simulate_external_close(self):
        self.stay_open = False
        self._closed_event.set()

    def push_message(self, msg):
        """Delivers a message to a receive_json() call that's currently
        blocked waiting -- models a real client sending something after a
        period of quiet, for testing that activity resets the idle clock."""
        self.incoming.append(msg)
        self._message_event.set()

    async def receive_json(self):
        while not self.incoming:
            if not self.stay_open:
                raise WebSocketDisconnectStub()
            wait_close = asyncio.create_task(self._closed_event.wait())
            wait_msg = asyncio.create_task(self._message_event.wait())
            try:
                await asyncio.wait({wait_close, wait_msg}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in (wait_close, wait_msg):
                    if not t.done():
                        t.cancel()
        msg = self.incoming.pop(0)
        if not self.incoming:
            self._message_event.clear()
        return msg

    async def send_json(self, data):
        self.sent.append(data)
        if self.send_behavior == "raises":
            raise RuntimeError("send failed -- socket already gone")
        if self.send_behavior == "hangs":
            await asyncio.sleep(999)


async def run_receive_and_heartbeat(ws, handle_message, heartbeat_interval, heartbeat_timeout,
                                     ping_timeout=1, task_holder=None):
    """Mirrors user_websocket_endpoint's Batch 3 logic (main.py, /ws/user):
    runs the message-receive loop and a heartbeat loop concurrently; the
    first one to raise wins and cancels the other. A stale/quiet connection
    (no message or pong within heartbeat_timeout) or a failed/hung ping
    send are both treated as a dead connection, exactly like a real
    WebSocketDisconnect.

    `task_holder`, if given, is filled with {"receive_task", "heartbeat_task"}
    purely so tests can assert on cancellation state even along the
    exception-raising path (main.py itself has no need for this hook).
    """
    last_activity = {"ts": time.time()}

    async def _receive_loop():
        while True:
            msg = await ws.receive_json()
            last_activity["ts"] = time.time()
            if isinstance(msg, dict) and msg.get("type") == "pong":
                continue
            await handle_message(msg)

    async def _heartbeat_loop():
        while True:
            await asyncio.sleep(heartbeat_interval)
            idle_for = time.time() - last_activity["ts"]
            if idle_for > heartbeat_timeout:
                raise ConnectionError(f"No client activity for {idle_for:.0f}s -- treating connection as dead")
            await asyncio.wait_for(ws.send_json({"type": "ping"}), timeout=ping_timeout)

    receive_task = asyncio.create_task(_receive_loop())
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    if task_holder is not None:
        task_holder["receive_task"] = receive_task
        task_holder["heartbeat_task"] = heartbeat_task

    done, pending = await asyncio.wait({receive_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except asyncio.CancelledError:
            pass
    for t in done:
        exc = t.exception()
        if exc is not None:
            raise exc
    return receive_task, heartbeat_task


class TestHealthyConnection(unittest.IsolatedAsyncioTestCase):
    async def test_normal_messages_are_dispatched_and_pongs_are_swallowed(self):
        handled = []

        async def handle_message(msg):
            handled.append(msg)

        ws = FakeUserWebSocket(incoming=[
            {"type": "pong"},
            {"type": "something"},
            {"type": "pong"},
        ], stay_open=True)

        # Drive it briefly then cancel from outside -- a genuinely healthy,
        # still-open connection has nothing that would make
        # run_receive_and_heartbeat return on its own.
        task = asyncio.create_task(
            run_receive_and_heartbeat(ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=10)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Only the non-pong message reached the app handler.
        self.assertEqual(handled, [{"type": "something"}])


class TestHeartbeatPingBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_pings_sent_periodically_while_client_is_quiet_but_alive(self):
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[], stay_open=True)  # alive, never sends anything

        task = asyncio.create_task(
            run_receive_and_heartbeat(ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=10)
        )
        await asyncio.sleep(0.22)  # ~4 heartbeat intervals, well under the 10s timeout
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        self.assertGreaterEqual(len(ws.sent), 3)
        for ping in ws.sent:
            self.assertEqual(ping, {"type": "ping"})

    async def test_message_activity_resets_the_idle_clock(self):
        """A message arriving partway through the timeout window must reset
        the idle clock -- proves real activity, not just pongs, counts, and
        that the timeout is measured from the LAST activity, not connection
        start."""
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[], stay_open=True)

        async def _push_later():
            await asyncio.sleep(0.15)  # partway through the 0.3s timeout window
            ws.push_message({"type": "hello"})

        pusher = asyncio.create_task(_push_later())
        task = asyncio.create_task(
            run_receive_and_heartbeat(ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=0.3)
        )
        # Total elapsed (0.35s) exceeds the timeout measured from connection
        # start (0.3s), but is well within it measured from the 0.15s reset
        # (0.20s idle < 0.3s timeout) -- a wide, non-flaky margin either way.
        await asyncio.sleep(0.35)
        self.assertFalse(task.done(), "activity from a real message should have reset the idle timer")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await pusher


class TestDeadConnectionCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_quiet_connection_exceeding_timeout_raises_connection_error(self):
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[], stay_open=True)

        with self.assertRaises(ConnectionError):
            await run_receive_and_heartbeat(ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=0.1)

    async def test_receive_loop_is_cancelled_once_heartbeat_declares_dead(self):
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[], stay_open=True)
        holder = {}

        with self.assertRaises(ConnectionError):
            await run_receive_and_heartbeat(
                ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=0.1, task_holder=holder
            )

        # The heartbeat loop is the one that raised; the receive loop
        # (still blocked on the never-closed fake socket) must have been
        # cancelled, not left running.
        self.assertTrue(holder["receive_task"].cancelled())
        self.assertTrue(holder["heartbeat_task"].done())
        self.assertIsInstance(holder["heartbeat_task"].exception(), ConnectionError)

    async def test_failed_ping_send_is_treated_as_dead(self):
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[], stay_open=True, send_behavior="raises")

        with self.assertRaises(RuntimeError):
            await run_receive_and_heartbeat(ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=10)

    async def test_hung_ping_send_is_bounded_by_ping_timeout(self):
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[], stay_open=True, send_behavior="hangs")

        start = time.time()
        with self.assertRaises(asyncio.TimeoutError):
            await run_receive_and_heartbeat(
                ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=10, ping_timeout=0.1
            )
        elapsed = time.time() - start
        self.assertLess(elapsed, 1.0, "a hung ping send must not block shutdown of this connection indefinitely")


class TestNormalDisconnect(unittest.IsolatedAsyncioTestCase):
    async def test_client_disconnect_propagates_and_stops_heartbeat(self):
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[{"type": "hi"}], stay_open=False)  # disconnects right after
        holder = {}

        with self.assertRaises(WebSocketDisconnectStub):
            await run_receive_and_heartbeat(
                ws, handle_message, heartbeat_interval=0.05, heartbeat_timeout=10, task_holder=holder
            )

        # No ping should have had time to fire before the (near-instant) disconnect.
        self.assertEqual(ws.sent, [])
        self.assertTrue(holder["heartbeat_task"].cancelled())


class TestShutdownRace(unittest.IsolatedAsyncioTestCase):
    async def test_server_initiated_close_mid_heartbeat_sleep_is_handled_cleanly(self):
        """Simulates Batch 2's shutdown path: something external closes the
        socket while the heartbeat loop is mid-sleep. The receive loop must
        notice and win the race immediately -- no hang, no orphaned
        heartbeat task."""
        async def handle_message(msg):
            pass

        ws = FakeUserWebSocket(incoming=[], stay_open=True)

        async def _simulate_shutdown_close():
            await asyncio.sleep(0.03)  # well inside the heartbeat's 1s sleep window
            ws.simulate_external_close()

        holder = {}
        closer = asyncio.create_task(_simulate_shutdown_close())
        start = time.time()
        with self.assertRaises(WebSocketDisconnectStub):
            await run_receive_and_heartbeat(
                ws, handle_message, heartbeat_interval=1, heartbeat_timeout=30, task_holder=holder
            )
        elapsed = time.time() - start
        self.assertLess(elapsed, 0.5, "an externally-closed socket must be noticed immediately, not on the next heartbeat tick")
        self.assertTrue(holder["heartbeat_task"].cancelled())
        await closer


if __name__ == "__main__":
    unittest.main()
