"""Phase 15A: /ws/user connection bounding (websocket_manager.UserConnectionManager).

Unlike main.py's dashboard ConnectionManager tests, websocket_manager.py has
no expensive import-time side effects, so this imports the real module
directly instead of mirroring it.
"""
import asyncio
import unittest

from websocket_manager import UserConnectionManager, MAX_USER_WS_TOTAL, MAX_USER_WS_PER_USER


class MockWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, message: dict):
        self.sent.append(message)


class TestUserConnectionManagerCaps(unittest.IsolatedAsyncioTestCase):
    async def test_connect_succeeds_under_caps(self):
        mgr = UserConnectionManager()
        ok = await mgr.connect(1, MockWebSocket())
        self.assertTrue(ok)
        self.assertEqual(len(mgr.active_connections[1]), 1)

    async def test_per_user_cap_rejects_without_registering(self):
        mgr = UserConnectionManager()
        for _ in range(MAX_USER_WS_PER_USER):
            self.assertTrue(await mgr.connect(1, MockWebSocket()))
        self.assertEqual(len(mgr.active_connections[1]), MAX_USER_WS_PER_USER)

        rejected = await mgr.connect(1, MockWebSocket())
        self.assertFalse(rejected)
        # The rejected socket must not have been appended.
        self.assertEqual(len(mgr.active_connections[1]), MAX_USER_WS_PER_USER)

    async def test_total_cap_rejects_a_new_user_even_under_their_own_per_user_cap(self):
        mgr = UserConnectionManager()
        # Fill MAX_USER_WS_TOTAL across many distinct users, each well under
        # their own per-user cap, to isolate the total-cap check.
        user_id = 0
        connected = 0
        while connected < MAX_USER_WS_TOTAL:
            self.assertTrue(await mgr.connect(user_id, MockWebSocket()))
            user_id += 1
            connected += 1

        self.assertEqual(mgr._total_connections(), MAX_USER_WS_TOTAL)
        rejected = await mgr.connect(user_id + 1, MockWebSocket())
        self.assertFalse(rejected)
        self.assertNotIn(user_id + 1, mgr.active_connections)

    def test_disconnect_removes_socket_and_prunes_empty_user_entry(self):
        mgr = UserConnectionManager()
        ws = MockWebSocket()
        mgr.active_connections[1] = [ws]
        mgr.disconnect(1, ws)
        self.assertNotIn(1, mgr.active_connections)

    def test_disconnect_of_unknown_socket_is_a_no_op(self):
        mgr = UserConnectionManager()
        # Must not raise even if the user/socket was never registered.
        mgr.disconnect(999, MockWebSocket())


if __name__ == "__main__":
    unittest.main()
