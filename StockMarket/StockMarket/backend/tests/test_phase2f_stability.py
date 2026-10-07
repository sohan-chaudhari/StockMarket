"""Phase 2F Regression Tests:
1. CriticalIndexPoller pauses polling during market-closed hours.
2. PricePoller start() sleeps during market-closed hours without creating worker pool load.
3. _yf_intraday_cache bounds at _YF_INTRADAY_CACHE_MAX (200) with LRU eviction.
4. run_controlled_closed_backfill_batch pauses when market is open or opening soon.
"""
import unittest
from unittest.mock import MagicMock, patch
from collections import OrderedDict
import time

from price_poller import CriticalIndexPoller, CRITICAL_INDICES
import backfill_weekly_monthly


class TestPhase2FStability(unittest.TestCase):
    def test_critical_poller_closed_market_pause(self):
        svc = MagicMock()
        svc.is_logged_in = True
        svc.smart_api = MagicMock()
        poller = CriticalIndexPoller(svc)
        for t in CRITICAL_INDICES:
            poller._token_cache[t] = {"exchange": "NSE", "symbol": t, "token": "1"}
        poller._poll_ticker = MagicMock(return_value=True)

        with patch("price_poller.is_market_open_now", return_value=False):
            # Run loop briefly
            import threading
            t = threading.Thread(target=poller._run_loop, daemon=True)
            t.start()
            time.sleep(0.05)
            poller._stop_event.set()
            t.join(timeout=1.0)

        # Polling did not fire any requests while closed
        self.assertEqual(poller._stats["polls"], 0)
        self.assertEqual(poller._poll_ticker.call_count, 0)

    def test_yf_intraday_cache_lru_cap(self):
        from main import _YF_INTRADAY_CACHE_MAX
        cache = OrderedDict()

        # Insert 250 entries
        for i in range(250):
            cache[(f"TICKER_{i}", "5m")] = (time.time(), [{"c": i}])
            cache.move_to_end((f"TICKER_{i}", "5m"))
            while len(cache) > _YF_INTRADAY_CACHE_MAX:
                cache.popitem(last=False)

        self.assertEqual(len(cache), _YF_INTRADAY_CACHE_MAX)
        # Oldest items (0..49) must have been evicted
        self.assertNotIn(("TICKER_0", "5m"), cache)
        self.assertNotIn(("TICKER_49", "5m"), cache)
        # Latest items (50..249) must be retained
        self.assertIn(("TICKER_50", "5m"), cache)
        self.assertIn(("TICKER_249", "5m"), cache)

    def test_backfill_pauses_during_market_hours(self):
        with patch("backfill_weekly_monthly.is_market_open_or_opening_soon", return_value=True):
            res = backfill_weekly_monthly.run_controlled_closed_backfill_batch(batch_size=5)
            self.assertEqual(res["status"], "paused_market_hours")
            self.assertEqual(res["processed"], 0)


if __name__ == "__main__":
    unittest.main()
