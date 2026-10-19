"""CRIT-03: the live REST pollers must not spend quota on quotes the WS already has.

One AngelOne account serves BOTH the live quotes and the historical API (no
HISTORICAL_API_KEY is configured -- historical_service.py:248 calls the two out as
"fighting"). Measured on production before this fix:

  * 10 "Rate limit hit -- pausing ALL historical calls for 90s" events in 20 minutes,
    i.e. ~15 of every 20 minutes spent inside a hard pause
  * 36 backfill attempts bounced off that cooldown ({'reason': 'cooldown'}), so a
    ticker the user opened genuinely could not be backfilled -- the gap only closed
    later, when a rare window opened
  * PricePoller: ~61 tickers x ~8 cycles/min ~= 470 REST quote calls/min
  * CriticalIndexPoller: 6 indices x ~2 polls/s ~= 720 REST calls/min -- and it
    checked WS freshness only AFTER calling ltpData(), so every one of those calls
    was made and then its result discarded

The gate now runs BEFORE the REST call (same threshold and same automatic fallback as
PricePoller): a WS-covered index costs zero REST calls, and if the WS tick goes stale
polling resumes, so no price can go missing -- it only stops re-fetching a value we
already have.
"""
import threading
import time
import unittest
from unittest import mock

import price_poller as pp


class _FakeService:
    def __init__(self, ticks=None, logged_in=True):
        self.latest_ticks = ticks if ticks is not None else {}
        self.latest_ticks_lock = threading.RLock()
        self.is_logged_in = logged_in
        self.smart_api = mock.MagicMock()
        self.smart_api.ltpData.return_value = {
            "status": True,
            "data": {"ltp": 100.0, "close": 99.0, "open": 98.0, "high": 101.0, "low": 97.0},
        }

    def get_token(self, ticker):
        return {"exchange": "NSE", "symbol": ticker, "token": "1"}


def _index_poller(svc, ticker="NIFTY"):
    p = pp.CriticalIndexPoller(svc, on_price_update=lambda *a, **k: None)
    p._token_cache[ticker] = {"exchange": "NSE", "symbol": ticker, "token": "1"}
    return p


def _price_poller(svc, ticker="TCS"):
    p = pp.PricePoller(svc, tickers=[ticker])
    p._token_cache[ticker] = {"exchange": "NSE", "symbol": ticker, "token": "1"}
    if not hasattr(p, "_api_sem"):
        p._api_sem = threading.Lock()
    return p


class CriticalIndexPollerGateTests(unittest.TestCase):
    def test_fresh_ws_tick_is_not_polled_over_rest(self):
        now = time.time()
        svc = _FakeService({"NIFTY": {"_source": "angel_ws", "_received_ts": now - 1, "current_price": 100.0}})
        p = _index_poller(svc)

        self.assertTrue(p._poll_ticker("NIFTY"))
        svc.smart_api.ltpData.assert_not_called()
        self.assertEqual(p._stats["polls"], 0, "a WS-covered index must cost no REST call")

    def test_stale_ws_tick_falls_back_to_rest(self):
        svc = _FakeService({"NIFTY": {"_source": "angel_ws",
                                      "_received_ts": time.time() - pp.WS_TICK_FRESH_SEC - 5,
                                      "current_price": 100.0}})
        p = _index_poller(svc)
        p._poll_ticker("NIFTY")
        svc.smart_api.ltpData.assert_called_once()
        self.assertEqual(p._stats["polls"], 0)

    def test_no_ws_tick_at_all_still_polls(self):
        p = _index_poller(_FakeService({}))
        p._poll_ticker("NIFTY")
        svc = p.service
        svc.smart_api.ltpData.assert_called_once()

    def test_a_non_ws_source_does_not_suppress_polling(self):
        """Only the WS counts as authoritative-fresh; our own previous poll must not
        stop the next one, or the price would freeze."""
        svc = _FakeService({"NIFTY": {"_source": "critical_poller", "_received_ts": time.time() - 1}})
        _index_poller(svc)._poll_ticker("NIFTY")
        svc.smart_api.ltpData.assert_called_once()

    def test_the_gate_runs_before_the_rest_call_in_the_source(self):
        """The bug was ordering: the check existed but only AFTER ltpData()."""
        import inspect
        src = inspect.getsource(pp.CriticalIndexPoller._poll_ticker)
        i_gate = src.find('_pre.get("_source") == "angel_ws"')
        # match the CALL, not the word in the explanatory comment above it
        i_call = src.find("svc.smart_api.ltpData(")
        self.assertNotEqual(i_gate, -1, "the pre-call gate must exist")
        self.assertNotEqual(i_call, -1)
        self.assertLess(i_gate, i_call, "the WS check must come BEFORE the REST call")


class PricePollerGateTests(unittest.TestCase):
    def test_fresh_ws_tick_skips_the_rest_call(self):
        svc = _FakeService({"TCS": {"_source": "angel_ws", "_received_ts": time.time() - 1, "current_price": 1.0}})
        p = _price_poller(svc)
        self.assertTrue(p._poll_one("TCS"))
        svc.smart_api.ltpData.assert_not_called()

    def test_stale_ws_tick_still_polls(self):
        svc = _FakeService({"TCS": {"_source": "angel_ws",
                                    "_received_ts": time.time() - pp.WS_TICK_FRESH_SEC - 5}})
        _price_poller(svc)._poll_one("TCS")
        svc.smart_api.ltpData.assert_called_once()


class SharedPolicyTests(unittest.TestCase):
    def test_bulk_poller_pacing_stays_high_enough_to_leave_quota_for_backfill(self):
        """A future 'make prices faster' edit must not silently re-saturate the
        account and starve backfill again (measured at 2s: ~1,800 quote calls/min and
        a permanent rate limit)."""
        self.assertGreaterEqual(pp.CYCLE_INTERVAL, 5.0, "bulk REST polling must stay paced")
        # the critical poller is fine at 0.5s ONLY because its WS gate skips covered
        # indices entirely -- assert that gate is still there
        import inspect
        self.assertIn("WS_TICK_FRESH_SEC", inspect.getsource(pp.CriticalIndexPoller._poll_ticker))

    def test_both_pollers_use_the_same_threshold_constant(self):
        import inspect
        self.assertIn("WS_TICK_FRESH_SEC", inspect.getsource(pp.PricePoller._poll_one))
        self.assertIn("WS_TICK_FRESH_SEC", inspect.getsource(pp.CriticalIndexPoller._poll_ticker))
        self.assertGreater(pp.WS_TICK_FRESH_SEC, 0)

    def test_only_two_rest_quote_call_sites_exist_and_both_are_gated(self):
        import inspect
        mod = inspect.getsource(pp)
        # count the real calls (the phrase also appears in the explanatory comments)
        self.assertEqual(mod.count("svc.smart_api.ltpData("), 2,
                         "a new ungated REST quote call would evade the policy")


if __name__ == "__main__":
    unittest.main()
