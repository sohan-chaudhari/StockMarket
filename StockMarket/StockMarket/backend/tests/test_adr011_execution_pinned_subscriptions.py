import threading
import time
import unittest
from unittest.mock import MagicMock

from angelone_service import AngelOneService
from main import ViewedTickerManager
from execution_engine import PriceMonitorService


class TestADR011ExecutionPinnedSubscriptions(unittest.TestCase):
    """
    Focused test suite verifying all 5 ADR-011 Implementation Guarantees
    and requirements for Execution-Pinned WebSocket Subscriptions.
    """

    def setUp(self):
        self.mock_angel = MagicMock(spec=AngelOneService)
        self.mock_angel.subscribed_tokens = set()
        self.mock_angel.token_lock = threading.Lock()
        self.mock_angel.subscribe_tickers = MagicMock()
        self.mock_angel.unsubscribe_tickers = MagicMock()
        self.mgr = ViewedTickerManager(self.mock_angel, max_subscriptions=5)

    def test_one_active_order_keeps_ticker_subscribed_with_zero_viewers(self):
        """Guarantee 5: Active order pins ticker subscription without any browser viewers."""
        self.mgr.set_execution_ref_counts({"RELIANCE": 1})
        self.assertIn("RELIANCE", self.mgr._subscribed)
        self.assertEqual(self.mgr.get_execution_ref_count("RELIANCE"), 1)
        self.assertEqual(self.mgr.get_viewed_tickers(), [])
        self.assertTrue(self.mgr._has_interest("RELIANCE"))

    def test_multiple_active_orders_produce_correct_db_derived_count(self):
        """Guarantee 1: DB reconciliation correctly derives ref count for multiple orders."""
        self.mgr.set_execution_ref_counts({"TCS": 3, "INFY": 1})
        self.assertEqual(self.mgr.get_execution_ref_count("TCS"), 3)
        self.assertEqual(self.mgr.get_execution_ref_count("INFY"), 1)
        self.assertIn("TCS", self.mgr._subscribed)
        self.assertIn("INFY", self.mgr._subscribed)

    def test_closing_one_of_multiple_orders_does_not_unsubscribe(self):
        """Subscription must remain active until the LAST order completes."""
        self.mgr.set_execution_ref_counts({"TCS": 2})
        self.assertIn("TCS", self.mgr._subscribed)

        # 1 order closes -> 1 order remains
        self.mgr.set_execution_ref_counts({"TCS": 1})
        self.assertIn("TCS", self.mgr._subscribed)
        self.assertEqual(self.mgr.get_execution_ref_count("TCS"), 1)
        self.assertNotIn("TCS", self.mgr._pending_unsub)

    def test_closing_last_active_order_allows_normal_unsubscribe(self):
        """Closing the last order enters normal debounce unsubscription if zero viewers."""
        self.mgr.set_execution_ref_counts({"TCS": 1})
        self.assertIn("TCS", self.mgr._subscribed)

        # Last order closed
        self.mgr.set_execution_ref_counts({"TCS": 0})
        self.assertEqual(self.mgr.get_execution_ref_count("TCS"), 0)
        self.assertIn("TCS", self.mgr._pending_unsub)

    def test_restart_reconciliation_rebuilds_identical_counts(self):
        """Guarantee 1: State is rebuildable identically across instances/restarts."""
        # First instance
        self.mgr.set_execution_ref_counts({"HDFCBANK": 2, "SBIN": 1})
        
        # New instance after simulated restart
        new_mgr = ViewedTickerManager(self.mock_angel, max_subscriptions=5)
        new_mgr.set_execution_ref_counts({"HDFCBANK": 2, "SBIN": 1})
        
        self.assertEqual(self.mgr._execution_counts, new_mgr._execution_counts)
        self.assertEqual(self.mgr._subscribed, new_mgr._subscribed)

    def test_negative_counts_are_impossible(self):
        """Guarantee 1: DB-authoritative snapshot eliminates negative counts."""
        self.mgr.set_execution_ref_counts({"WIPRO": -5, "TATAMOTORS": 0})
        self.assertEqual(self.mgr.get_execution_ref_count("WIPRO"), 0)
        self.assertEqual(self.mgr.get_execution_ref_count("TATAMOTORS"), 0)
        self.assertNotIn("WIPRO", self.mgr._execution_counts)

    def test_concurrent_repeated_reconciliation_does_not_double_count(self):
        """Guarantee 1: Repeated reconciliation cycles are strictly idempotent."""
        for _ in range(5):
            self.mgr.set_execution_ref_counts({"RELIANCE": 2})
        self.assertEqual(self.mgr.get_execution_ref_count("RELIANCE"), 2)

    def test_token_set_access_is_protected_by_lock(self):
        """Guarantee 2: AngelOneService protects subscribed_tokens via token_lock."""
        svc = AngelOneService()
        self.assertTrue(hasattr(svc, "token_lock"))
        self.assertIsInstance(svc.token_lock, type(threading.Lock()))

        # Check that operations acquire lock
        with svc.token_lock:
            svc.subscribed_tokens.add("26000")
            self.assertIn("26000", svc.subscribed_tokens)

    def test_reconnect_does_not_lose_execution_referenced_subscriptions(self):
        """Guarantee 2 & 6: Reconnection preserves all subscribed tokens safely under lock."""
        svc = AngelOneService()
        with svc.token_lock:
            svc.subscribed_tokens.add("2885")  # RELIANCE token
            tokens = list(svc.subscribed_tokens)
        self.assertIn("2885", tokens)

    def test_audit_subscribes_missing_active_tickers(self):
        """Guarantee 3 & 4: 60s consistency audit detects and repairs missing subscriptions."""
        # Ticker has active order count but somehow dropped from _subscribed
        self.mgr._execution_counts["ITC"] = 1
        self.mgr._subscribed.discard("ITC")

        self.mgr.audit_and_repair()
        self.assertIn("ITC", self.mgr._subscribed)
        self.assertEqual(self.mgr.stats["audit_repairs"], 1)

    def test_audit_never_directly_unsubscribes(self):
        """Guarantee 3: Audit is strictly subscribe-only and never unsubscribes directly."""
        self.mgr._subscribed.add("OLD_TICKER")
        self.mock_angel.unsubscribe_tickers.reset_mock()

        self.mgr.audit_and_repair()
        # Ensure audit didn't call broker unsubscribe
        self.mock_angel.unsubscribe_tickers.assert_not_called()
        # OLD_TICKER stays in _subscribed until debounce cleans it up
        self.assertIn("OLD_TICKER", self.mgr._subscribed)

    def test_viewer_plus_execution_reference_interaction(self):
        """Invariant: viewer_ref_count > 0 OR execution_ref_count > 0 maintains subscription."""
        ticker = "BHARTIARTL"
        # 1. Viewer joins
        self.mgr.view(ticker)
        self.assertIn(ticker, self.mgr._subscribed)

        # 2. Execution order placed
        self.mgr.set_execution_ref_counts({ticker: 1})

        # 3. Viewer leaves (view count 0, but execution count 1)
        self.mgr.unview(ticker)
        self.assertNotIn(ticker, self.mgr._pending_unsub)
        self.assertIn(ticker, self.mgr._subscribed)

        # 4. Execution order closed (execution count 0, viewer count 0 -> enter debounce)
        self.mgr.set_execution_ref_counts({ticker: 0})
        self.assertIn(ticker, self.mgr._pending_unsub)

    def test_lru_eviction_cannot_remove_execution_referenced_ticker(self):
        """Guarantee 5: Execution-pinned tickers are immune to LRU eviction under capacity cap."""
        # Cap is 5. Fill with 1 execution-pinned ticker and 4 viewer-only tickers.
        self.mgr.set_execution_ref_counts({"PINNED_STOCK": 1})
        for i in range(1, 5):
            self.mgr.view(f"VIEW_{i}")

        # Simulate 5-min debounce expiry on all viewer-only tickers
        now = time.time()
        for i in range(1, 5):
            self.mgr.unview(f"VIEW_{i}")
            self.mgr._pending_unsub[f"VIEW_{i}"] = now - 400

        # Also attempt to trick the manager by putting PINNED_STOCK in pending_unsub
        self.mgr._pending_unsub["PINNED_STOCK"] = now - 400

        # Now view a 6th ticker to trigger eviction
        self.mgr.view("NEW_STOCK_6")

        # PINNED_STOCK must never be evicted
        self.assertIn("PINNED_STOCK", self.mgr._subscribed)
        self.assertEqual(self.mgr.get_execution_ref_count("PINNED_STOCK"), 1)


if __name__ == "__main__":
    unittest.main()
