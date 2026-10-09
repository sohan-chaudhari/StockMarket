"""Market-movers stale-tick gate regression.

Some illiquid tokens get a *replayed stale snapshot* from the broker WebSocket:
the price/prev_close/volume belong to a past session (e.g. late September) while
the packet still arrives "now". `_on_angel_tick` used to overwrite the broker
timestamp with server time, so /api/market-movers and /api/live-prices trusted
those quotes and showed a past session's move as today's -- and the gainers /
losers list was dominated by tickers that never actually moved.

The fix:
  * `_on_angel_tick` preserves the broker timestamp as `_exch_ts`;
  * `_tick_is_current_session()` rejects a quote whose `_exch_ts` is not from
    the current session;
  * `_get_all_market_prices` skips stale ticks (marks live ones `_live`);
  * `_build_movers` only lists current-session quotes while the market is open.
"""
import time
import unittest
from datetime import datetime, timedelta

import main
from angelone_service import angelone_service as svc

IST = main.IST
TICKER = "ZZMOVERSGATE"


def _epoch(dt):
    return dt.timestamp()


class TickSessionGateTests(unittest.TestCase):
    def test_missing_timestamp_is_trusted(self):
        self.assertTrue(main._tick_is_current_session({}, True))
        self.assertTrue(main._tick_is_current_session({"_exch_ts": None}, True))

    def test_bogus_timestamp_is_trusted(self):
        self.assertTrue(main._tick_is_current_session({"_exch_ts": 0}, True))
        self.assertTrue(main._tick_is_current_session({"_exch_ts": 123}, True))
        self.assertTrue(main._tick_is_current_session({"_exch_ts": "nan"}, True))

    def test_previous_day_is_stale(self):
        y = datetime.now(IST) - timedelta(days=1)
        self.assertFalse(main._tick_is_current_session({"_exch_ts": _epoch(y)}, True))
        self.assertFalse(main._tick_is_current_session({"_exch_ts": _epoch(y)}, False))

    def test_pre_open_same_day_is_stale_while_market_open(self):
        pre = datetime.now(IST).replace(hour=6, minute=55, second=0, microsecond=0)
        self.assertFalse(main._tick_is_current_session({"_exch_ts": _epoch(pre)}, True))
        # market closed: only the date matters, so same-day pre-open is trusted
        self.assertTrue(main._tick_is_current_session({"_exch_ts": _epoch(pre)}, False))

    def test_intraday_is_fresh(self):
        intraday = datetime.now(IST).replace(hour=10, minute=30, second=0, microsecond=0)
        self.assertTrue(main._tick_is_current_session({"_exch_ts": _epoch(intraday)}, True))


class ExchTsPreservedTests(unittest.TestCase):
    def tearDown(self):
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)

    def test_on_angel_tick_preserves_exch_ts(self):
        exch = time.time() - 3600
        main._on_angel_tick(TICKER, {
            "current_price": 100.0, "prev_close": 99.0, "volume": 5,
            "_ts": exch, "_source": "angel_ws",
        })
        with svc.latest_ticks_lock:
            t = dict(svc.latest_ticks[TICKER])
        self.assertAlmostEqual(t["_exch_ts"], exch)
        self.assertGreaterEqual(t["_received_ts"], exch)  # server receive time


class OverlayGateTests(unittest.TestCase):
    def setUp(self):
        self._orig_baseline = main._db_baseline_prices
        self._orig_open = main.is_market_open_now
        main.is_market_open_now = lambda: True
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)

    def tearDown(self):
        main._db_baseline_prices = self._orig_baseline
        main.is_market_open_now = self._orig_open
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)

    def _set_baseline(self):
        main._db_baseline_prices = {
            TICKER: {
                "current": 100.0, "current_price": 100.0, "open": 99.0, "high": 101.0,
                "low": 98.0, "prev_close": 95.0, "change": 5.0, "change_pct": 5.26,
                "volume": 1000, "prev_volume": 800, "vol_surge": 1.25,
                "timestamp": datetime.now(IST).replace(tzinfo=None) - timedelta(days=1),
            }
        }

    def test_stale_tick_is_not_overlaid(self):
        self._set_baseline()
        with svc.latest_ticks_lock:
            svc.latest_ticks[TICKER] = {
                "current_price": 167.46, "prev_close": 152.24, "volume": 1078676,
                "_exch_ts": _epoch(datetime.now(IST) - timedelta(days=11)),
                "_received_ts": time.time(), "_source": "angel_ws",
            }
        out = main._get_all_market_prices()[TICKER]
        self.assertAlmostEqual(out["current"], 100.0)   # baseline kept, tick ignored
        self.assertFalse(out.get("_live"))

    def test_fresh_tick_is_overlaid(self):
        self._set_baseline()
        with svc.latest_ticks_lock:
            svc.latest_ticks[TICKER] = {
                "current_price": 105.0, "prev_close": 100.0, "volume": 5000,
                "_exch_ts": _epoch(datetime.now(IST)),
                "_received_ts": time.time(), "_source": "angel_ws",
            }
        out = main._get_all_market_prices()[TICKER]
        self.assertAlmostEqual(out["current"], 105.0)
        self.assertTrue(out.get("_live"))


class BuildMoversLiveOnlyTests(unittest.TestCase):
    def setUp(self):
        self._orig_open = main.is_market_open_now

    def tearDown(self):
        main.is_market_open_now = self._orig_open

    def _prices(self):
        today = datetime.now(IST).replace(tzinfo=None)
        return {
            "ZZLIVE1": {
                "current": 110.0, "current_price": 110.0, "open": 100.0, "high": 111.0,
                "low": 99.0, "prev_close": 100.0, "change": 10.0, "change_pct": 10.0,
                "volume": 100000, "prev_volume": 50000, "vol_surge": 2.0,
                "_live": True, "timestamp": today,
            },
            "ZZSTALE1": {
                "current": 120.0, "current_price": 120.0, "open": 100.0, "high": 121.0,
                "low": 99.0, "prev_close": 100.0, "change": 20.0, "change_pct": 20.0,
                "volume": 100000, "prev_volume": 50000, "vol_surge": 2.0,
                "timestamp": today - timedelta(days=1),
            },
        }

    def test_market_open_excludes_non_live(self):
        main.is_market_open_now = lambda: True
        d = main._build_movers(self._prices(), cap_filter="all")
        tickers = [x["ticker"] for x in d["gainers"]]
        self.assertIn("ZZLIVE1", tickers)
        self.assertNotIn("ZZSTALE1", tickers)

    def test_market_closed_includes_baseline(self):
        main.is_market_open_now = lambda: False
        d = main._build_movers(self._prices(), cap_filter="all")
        tickers = [x["ticker"] for x in d["gainers"]]
        self.assertIn("ZZLIVE1", tickers)
        self.assertIn("ZZSTALE1", tickers)

    def test_market_open_with_no_live_quotes_falls_back_to_baseline(self):
        main.is_market_open_now = lambda: True
        prices = self._prices()
        prices["ZZLIVE1"].pop("_live")  # simulate the live feed being down
        d = main._build_movers(prices, cap_filter="all")
        tickers = [x["ticker"] for x in d["gainers"]]
        self.assertIn("ZZSTALE1", tickers)


if __name__ == "__main__":
    unittest.main()
