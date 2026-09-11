"""Phase 15A: unsubscribe_tickers() must prune the ancillary maps it never
touched before (token_to_ticker_map, token_to_exch_type_map,
subscription_times, latest_ticks) -- otherwise is_subscription_ready()
keeps reporting an unsubscribed ticker as "ready" forever on its last
frozen tick instead of falling back to REST.

Same singleton-override pattern as test_angelone_reconnect.py.
"""
import threading
import unittest
from unittest.mock import patch

from angelone_service import AngelOneService


class TestUnsubscribeCleansAncillaryMaps(unittest.TestCase):
    def setUp(self):
        self.svc = AngelOneService()
        self.svc.token_lock = threading.Lock()
        self.svc.latest_ticks_lock = threading.Lock()
        self.svc.sws = None  # skip the real broker unsubscribe() call
        self.svc.ws_connected = False
        self.svc.subscribed_tokens = {"12345"}
        self.svc.token_to_ticker_map = {"12345": "RELIANCE"}
        self.svc.token_to_exch_type_map = {"12345": 1}
        self.svc.subscription_times = {"RELIANCE": 1234.0}
        self.svc.latest_ticks = {"RELIANCE": {"current_price": 2500.0}}

    def test_unsubscribe_prunes_all_ancillary_maps(self):
        with patch.object(self.svc, "get_token", return_value={"token": "12345"}):
            self.svc.unsubscribe_tickers(["RELIANCE"])

        self.assertNotIn("12345", self.svc.subscribed_tokens)
        self.assertNotIn("12345", self.svc.token_to_ticker_map)
        self.assertNotIn("12345", self.svc.token_to_exch_type_map)
        self.assertNotIn("RELIANCE", self.svc.subscription_times)
        self.assertNotIn("RELIANCE", self.svc.latest_ticks)

    def test_is_subscription_ready_falls_back_to_rest_after_unsubscribe(self):
        # Before unsubscribe: a live tick makes it immediately "ready".
        self.assertTrue(self.svc.is_subscription_ready("RELIANCE"))

        with patch.object(self.svc, "get_token", return_value={"token": "12345"}):
            self.svc.unsubscribe_tickers(["RELIANCE"])

        # After unsubscribe: no stale tick, no subscription_times entry
        # either -- must report not-ready (caller falls back to REST),
        # not "ready" on a frozen price from before the unsubscribe.
        self.assertFalse(self.svc.is_subscription_ready("RELIANCE"))

    def test_unsubscribe_of_untracked_ticker_is_a_no_op(self):
        with patch.object(self.svc, "get_token", return_value=None):
            self.svc.unsubscribe_tickers(["NOT_A_REAL_TICKER"])  # must not raise
        self.assertEqual(self.svc.subscribed_tokens, {"12345"})


if __name__ == "__main__":
    unittest.main()
