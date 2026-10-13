"""Regression tests for the two issues reported after the hover fix.

BUG 1 -- hovering a candle showed its own OHLC and then flipped to the CURRENT
    candle's OHLC after ~2 seconds.
    Lightweight Charts fires subscribeCrosshairMove on every series DATA UPDATE
    with an empty point/time, so the "pointer left the bar" branch ran on each
    live tick: it cleared the hover lock and repainted the forming candle. Legend
    ownership is now driven by real pointer presence on the chart element
    (mouseenter/mousemove/mouseleave) instead of the event payload.

BUG 2 -- switching to the 5m timeframe did not show the backfill; only a manual
    page refresh did.
    The backend dispatches the gap fill asynchronously, and the client already
    re-polls to merge the recovered bars -- but nginx's 10s micro-cache
    (proxy_cache_valid 200 10s, keyed by full URI) answered that re-poll with the
    PRE-fill body. Chart-data responses now forbid caching (Cache-Control no-store
    + nginx's X-Accel-Expires:0), so every chart load reaches the app.
"""
import os
import unittest

FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "dashboard.js")


class HoverLegendOwnershipTests(unittest.TestCase):
    def setUp(self):
        with open(FRONTEND, "r", encoding="utf-8") as f:
            self.src = f.read()

    def test_pointer_presence_is_tracked(self):
        self.assertIn("window._crosshairMouseInside = false;", self.src)
        self.assertIn("el.addEventListener('mouseenter'", self.src)
        self.assertIn("el.addEventListener('mouseleave'", self.src)

    def test_mouseleave_releases_the_legend(self):
        i = self.src.find("el.addEventListener('mouseleave'")
        self.assertNotEqual(i, -1)
        block = self.src[i:i + 400]
        self.assertIn("window._crosshairMouseInside = false;", block)
        self.assertIn("window._crosshairBarActive = false;", block)

    def test_data_update_does_not_steal_the_legend(self):
        # The empty-point branch must bail out while the pointer is on the chart.
        i = self.src.find("if (!param.time || param.point.x < 0 || param.point.y < 0) {")
        self.assertNotEqual(i, -1)
        block = self.src[i:i + 700]
        self.assertIn("if (window._crosshairMouseInside) return;", block,
                      "a data update must not repaint the forming candle while hovering")

    def test_forming_candle_paint_is_centralised(self):
        self.assertIn("function _renderLegendIdle()", self.src)
        # and it is only reached when the pointer is genuinely gone
        self.assertLess(self.src.count("_renderLegendIdle();"), 4)

    def test_new_range_releases_ownership(self):
        i = self.src.find("var _veilNeeded = !window._chartHasRendered;")
        self.assertNotEqual(i, -1)
        block = self.src[i:i + 800]
        self.assertIn("window._crosshairBarActive = false;", block)
        # ...but only for a real range change -- a plain reload (the boundary
        # reconciliation and the backfill re-poll call loadData too) must not
        # steal a hovered bar's OHLC.
        self.assertIn("if (window._loadedRange !== range)", block)


class ChartDataCacheBypassTests(unittest.TestCase):
    def test_stock_data_responses_forbid_caching(self):
        from fastapi.testclient import TestClient
        import main
        client = TestClient(main.app)
        r = client.get("/api/stock-data/__no_such_route__")
        self.assertEqual(r.headers.get("x-accel-expires"), "0",
                         "nginx must be told not to cache chart data")
        self.assertIn("no-store", r.headers.get("cache-control", ""))

    def test_other_api_paths_are_unaffected(self):
        from fastapi.testclient import TestClient
        import main
        client = TestClient(main.app)
        r = client.get("/api/__nope__")
        self.assertNotEqual(r.headers.get("x-accel-expires"), "0")


if __name__ == "__main__":
    unittest.main()
