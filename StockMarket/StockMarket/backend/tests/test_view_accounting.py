"""Phase 2 (A1) regression: dashboard ticker view accounting is exactly-once.

The dashboard client sends BOTH `subscribe` and `view_ticker` for the same
ticker (frontend/ws.js) and re-sends both on every reconnect. Each handler used
to call `ViewedTickerManager.view()` independently, so the per-ticker reference
count reached 2 while teardown (`_cleanup_dashboard_client` -> `unview_all`)
released it only once -- the count never returned to 0, the ticker was never
unsubscribed, and every reconnect leaked another reference.

`_acquire_ticker_view` / `_release_ticker_view` make the acquire and release
balanced one-for-one per (connection, ticker), keyed on
`manager.user_topics[client_id]`. These tests exercise the real helpers and the
real `ViewedTickerManager` (with a stub AngelOne service).
"""
import unittest

import main


class _StubAngel:
    """Records subscribe/unsubscribe calls without touching the broker."""

    def __init__(self):
        self.subscribed = []
        self.unsubscribed = []

    def subscribe_tickers(self, tickers):
        self.subscribed.extend(tickers)

    def unsubscribe_tickers(self, tickers):
        self.unsubscribed.extend(tickers)


def _mgr(max_subs=50):
    return main.ViewedTickerManager(_StubAngel(), max_subscriptions=max_subs)


class AcquireReleaseBalanceTests(unittest.TestCase):
    def setUp(self):
        self.mgr = _mgr()
        self.topics = {"c1": set(), "c2": set()}

    def _acquire(self, cid, tk):
        return main._acquire_ticker_view(cid, tk, self.topics, self.mgr)

    def _release(self, cid, tk):
        return main._release_ticker_view(cid, tk, self.topics, self.mgr)

    def test_subscribe_then_view_ticker_counts_once(self):
        # `subscribe` grants the ticker...
        self.assertTrue(self._acquire("c1", "SBIN"))
        # ...the `view_ticker` message the frontend also sends must NOT add a
        # second reference.
        self.assertFalse(self._acquire("c1", "SBIN"))
        self.assertEqual(self.mgr._view_counts.get("SBIN"), 1)

    def test_duplicate_view_ticker_is_idempotent(self):
        self.assertTrue(self._acquire("c1", "SBIN"))
        self.assertFalse(self._acquire("c1", "SBIN"))
        self.assertFalse(self._acquire("c1", "SBIN"))
        self.assertEqual(self.mgr._view_counts.get("SBIN"), 1)

    def test_release_is_balanced_with_acquire(self):
        self._acquire("c1", "SBIN")
        self.assertTrue(self._release("c1", "SBIN"))
        self.assertNotIn("SBIN", self.mgr._view_counts)
        # a second release (already gone) must NOT decrement again
        self.assertFalse(self._release("c1", "SBIN"))
        self.assertNotIn("SBIN", self.mgr._view_counts)

    def test_release_of_untracked_ticker_is_noop(self):
        self.assertFalse(self._release("c1", "SBIN"))
        self.assertNotIn("SBIN", self.mgr._view_counts)

    def test_two_clients_each_count_once(self):
        self._acquire("c1", "SBIN")
        self._acquire("c2", "SBIN")
        self.assertEqual(self.mgr._view_counts.get("SBIN"), 2)
        # c1 leaves; c2 is still viewing -> the count must stay >= 1 so c2 is
        # NOT unsubscribed.
        self.assertTrue(self._release("c1", "SBIN"))
        self.assertEqual(self.mgr._view_counts.get("SBIN"), 1)
        self.assertTrue(self.mgr._has_interest("SBIN"))
        self.assertTrue(self._release("c2", "SBIN"))
        self.assertFalse(self.mgr._has_interest("SBIN"))

    def test_disconnect_releases_each_view_once(self):
        # one connection subscribes+views two tickers (each with a duplicate
        # view_ticker message).
        for tk in ("SBIN", "TCS"):
            self._acquire("c1", tk)
            self._acquire("c1", tk)
        # teardown path used by _cleanup_dashboard_client
        self.mgr.unview_all(list(self.topics["c1"]))
        self.assertNotIn("SBIN", self.mgr._view_counts)
        self.assertNotIn("TCS", self.mgr._view_counts)

    def test_reconnect_does_not_leak(self):
        # old connection acquires, then disconnects
        self._acquire("c1", "SBIN")
        self.mgr.unview_all(list(self.topics["c1"]))
        self.assertNotIn("SBIN", self.mgr._view_counts)
        # new connection (new client_id) after a reconnect, sending both
        # subscribe and view_ticker again
        self.topics["c3"] = set()
        self._acquire("c3", "SBIN")
        self._acquire("c3", "SBIN")
        self.assertEqual(self.mgr._view_counts.get("SBIN"), 1)

    def test_unknown_client_is_ignored(self):
        self.assertFalse(main._acquire_ticker_view("nope", "SBIN", self.topics, self.mgr))
        self.assertFalse(main._release_ticker_view("nope", "SBIN", self.topics, self.mgr))
        self.assertNotIn("SBIN", self.mgr._view_counts)

    def test_none_manager_still_tracks_topics(self):
        # mgr=None must not raise; the per-connection set is still maintained.
        self.assertTrue(main._acquire_ticker_view("c1", "SBIN", self.topics, None))
        self.assertIn("SBIN", self.topics["c1"])
        self.assertFalse(main._acquire_ticker_view("c1", "SBIN", self.topics, None))
        self.assertTrue(main._release_ticker_view("c1", "SBIN", self.topics, None))
        self.assertNotIn("SBIN", self.topics["c1"])


class HandlerWiringTests(unittest.TestCase):
    """The four WS handlers must route through the exactly-once helpers."""

    def test_handlers_use_the_helpers(self):
        import inspect
        src = inspect.getsource(main.dashboard_websocket)
        self.assertIn("_acquire_ticker_view(client_id, t,", src)
        self.assertIn("_acquire_ticker_view(client_id, tkr,", src)
        self.assertIn("_release_ticker_view(client_id, t,", src)
        self.assertIn("_release_ticker_view(client_id, tkr,", src)
        # the old unconditional calls must be gone
        self.assertNotIn("viewed_ticker_mgr.view(", src)
        self.assertNotIn("viewed_ticker_mgr.unview(", src)


if __name__ == "__main__":
    unittest.main()
