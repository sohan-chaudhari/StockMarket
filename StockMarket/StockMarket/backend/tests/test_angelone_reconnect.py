"""Phase 1b regression tests: RT-07 (leaked WS socket on relogin) and
RT-08 (blocking login stalls the event loop).

Uses the same singleton-override pattern as test_angelone_tick_dedup.py:
AngelOneService() returns the process-wide singleton, so tests overwrite
the specific attributes they need in setUp rather than assuming a clean
instance.
"""
import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from angelone_service import AngelOneService
from SmartApi.smartWebSocketV2 import SmartWebSocketV2


class TestOldSocketClosedOnReinit(unittest.TestCase):
    """RT-07: _init_websocket() must close the previous self.sws before
    replacing it, or a relogin (e.g. the 12h proactive refresh) leaves the
    old socket's connect() loop running forever with nothing referencing it.

    SMOKE-01: a real production smoke test (Batch 2 shutdown, run against an
    actual live AngelOne connection) proved SmartWebSocketV2 has NO close()
    method at all -- the real method is close_connection(). The original
    version of this test used a bare MagicMock() for old_sws, which happily
    accepts a call to .close() regardless of whether the real class has
    that method, so it never caught that the app code was calling a
    nonexistent method (silently swallowed by _init_websocket's own
    `except Exception: pass`). Now uses `spec=SmartWebSocketV2` so calling a
    method the real class doesn't have raises AttributeError in the test
    itself, the same way it would against the real object.
    """

    def setUp(self):
        self.svc = AngelOneService()
        self.svc.feed_token = "ft"
        self.svc.client_id = "cid"
        self.svc.api_key = "key"
        self.svc.jwt_token = "jwt"
        self.svc.token_lock = threading.Lock()
        self.svc.subscribed_tokens = set()

    def test_previous_socket_is_closed_before_replacement(self):
        old_sws = MagicMock(spec=SmartWebSocketV2)
        self.svc.sws = old_sws
        new_sws = MagicMock()

        with patch("angelone_service.SmartWebSocketV2", return_value=new_sws):
            self.svc._init_websocket()

        old_sws.close_connection.assert_called_once()
        self.assertIs(self.svc.sws, new_sws)

    def test_calling_close_on_a_spec_mock_would_have_caught_this_bug(self):
        """Meta-regression: proves spec=SmartWebSocketV2 actually enforces
        the real API surface, so this exact class of bug (calling a method
        that doesn't exist on the real object) can't silently pass again."""
        old_sws = MagicMock(spec=SmartWebSocketV2)
        with self.assertRaises(AttributeError):
            old_sws.close()  # the bug that shipped: this method doesn't exist

    def test_first_ever_init_does_not_error_with_no_prior_socket(self):
        self.svc.sws = None
        new_sws = MagicMock()

        with patch("angelone_service.SmartWebSocketV2", return_value=new_sws):
            self.svc._init_websocket()  # must not raise

        self.assertIs(self.svc.sws, new_sws)

    def test_close_error_on_old_socket_does_not_block_reinit(self):
        old_sws = MagicMock(spec=SmartWebSocketV2)
        old_sws.close_connection.side_effect = Exception("already dead")
        self.svc.sws = old_sws
        new_sws = MagicMock()

        with patch("angelone_service.SmartWebSocketV2", return_value=new_sws):
            self.svc._init_websocket()  # must not raise despite close_connection() failing

        self.assertIs(self.svc.sws, new_sws)


class TestEnsureConnectionDoesNotBlockEventLoop(unittest.TestCase):
    """RT-08: ensure_connection() must run login() (blocking network I/O) via
    asyncio.to_thread, not call it directly on the event loop.
    """

    def setUp(self):
        self.svc = AngelOneService()
        self.svc.login = MagicMock(return_value=True)

    def test_invalid_session_routes_login_through_to_thread(self):
        self.svc.is_logged_in = False
        self.svc.smart_api = None
        self.svc.jwt_token = None

        with patch("angelone_service.asyncio.to_thread", new=AsyncMock(return_value=True)) as mock_to_thread:
            result = asyncio.run(self.svc.ensure_connection())

        mock_to_thread.assert_called_once_with(self.svc.login)
        self.assertTrue(result)

    def test_proactive_relogin_routes_login_through_to_thread(self):
        self.svc.is_logged_in = True
        self.svc.smart_api = MagicMock()
        self.svc.jwt_token = "jwt"
        self.svc._last_login_time = time.time() - 43201  # just over 12h

        with patch("angelone_service.asyncio.to_thread", new=AsyncMock(return_value=True)) as mock_to_thread:
            result = asyncio.run(self.svc.ensure_connection())

        mock_to_thread.assert_called_once_with(self.svc.login)
        self.assertTrue(result)

    def test_healthy_session_never_touches_login(self):
        self.svc.is_logged_in = True
        self.svc.smart_api = MagicMock()
        self.svc.jwt_token = "jwt"
        self.svc._last_login_time = time.time()  # fresh, well under 12h

        with patch("angelone_service.asyncio.to_thread", new=AsyncMock()) as mock_to_thread:
            result = asyncio.run(self.svc.ensure_connection())

        mock_to_thread.assert_not_called()
        self.svc.login.assert_not_called()
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
