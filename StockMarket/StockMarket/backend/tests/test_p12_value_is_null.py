"""P1.2 — Lightweight Charts `Value is null` regression tests.

Root cause (proven): Lightweight Charts' candlestick/bar colorer runs
`f(d.Ot[0]) / f(d.Ot[3])` (ensureNotNull) for open/close during PAINT. A bar whose
`open` or `close` is `null` therefore throws `Uncaught Error: Value is null` on the
next repaint (`Value is undefined` for `undefined`). The initial-load path dropped
such bars (`.filter(p => p.open > 0 && ...)`), but the lazy-load merge in
`_fetchMoreData` did not. (The WS-reconnect path no longer raw-merges -- it
delegates to generation-safe loadData()/_renderChartData(); see
frontend/tests/test_ws_reconnect_backfill.cjs.)

These tests:
  1. prove the mechanism against the shipped lightweight-charts.js (control), and
  2. prove the shipped `_sanitizeOHLCV` boundary helper keeps such bars out of the
     series (no error at paint),
plus static contract checks on the shipped frontend.
"""
import functools
import http.server
import json
import os
import socketserver
import threading
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "frontend")
DASH = os.path.join(FRONTEND_DIR, "dashboard.js")
MARKET = os.path.join(FRONTEND_DIR, "market.html")

HTML_PAGES = [
    "home.html", "index.html", "stock.html", "chart.html", "tv-chart.html",
    "index_chart.html", "stock_restructured.html", "stock_rebuilt.html",
]

try:
    from playwright.sync_api import sync_playwright
    _HAS_PW = True
except Exception:
    _HAS_PW = False


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(src, name):
    idx = src.index("function " + name + "(")
    i = src.index("{", idx)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[idx:j + 1]
        j += 1
    raise AssertionError("unbalanced braces extracting " + name)


class ValueIsNullStaticTests(unittest.TestCase):
    def setUp(self):
        self.dash = _read(DASH)
        self.mkt = _read(MARKET)

    def test_sanitizer_defined_once_in_dashboard(self):
        self.assertEqual(self.dash.count("function _sanitizeOHLCV("), 1)

    def test_sanitizer_applied_at_lazy_load_merge(self):
        # 1 definition + 1 use. The only remaining raw REST->series merge in
        # dashboard.js is the lazy-load _fetchMoreData path.
        self.assertEqual(self.dash.count("_sanitizeOHLCV("), 2)

    def test_reconnect_path_no_longer_raw_merges(self):
        # P1.2 residual (Issue-2): the WS-reconnect handler must not raw-merge
        # epoch bars into the index-based cache; it delegates to loadData(), whose
        # _renderChartData() already filters invalid OHLC (and which is
        # generation-guarded). Covered end-to-end by
        # frontend/tests/test_ws_reconnect_backfill.cjs.
        src = self.dash
        i = src.index("_bigChartWsHandler = function wsBigChart")
        j = src.index("window.addEventListener('dashboard_price_update', window._bigChartWsHandler)", i)
        body = src[i:j]
        self.assertIn("window.loadData(activeRange)", body)
        self.assertNotIn("fetch('/api/stock-data/intraday/since", body)
        self.assertNotIn("fetch('/api/stock-data/candle/latest", body)
        self.assertNotIn("_sanitizeOHLCV(", body)  # no raw merge left to sanitize

    def test_sanitizer_requires_finite_positive_ohlc(self):
        src = _extract_function(self.dash, "_sanitizeOHLCV")
        self.assertIn("isFinite(o)", src)
        self.assertIn("o > 0 && h > 0 && l > 0 && c > 0", src)

    def test_market_html_sanitizes_lazy_merge(self):
        self.assertEqual(self.mkt.count("function _sanitizeOHLCV("), 1)
        self.assertIn("var s=_sanitizeOHLCV(p); if(!s)return null;", self.mkt)

    def test_dashboard_cache_busted(self):
        for page in HTML_PAGES:
            body = _read(os.path.join(FRONTEND_DIR, page))
            self.assertIn("dashboard.js?v=1067", body, page)
            self.assertNotIn("dashboard.js?v=1066", body, page)


@unittest.skipUnless(_HAS_PW, "playwright not available")
class ValueIsNullBrowserTests(unittest.TestCase):
    """Loads the shipped lightweight-charts.js and drives a real paint."""

    @classmethod
    def setUpClass(cls):
        cls.sanitizer = _extract_function(_read(DASH), "_sanitizeOHLCV")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=FRONTEND_DIR)
        cls.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.html_path = os.path.join(FRONTEND_DIR, "_p12_test.html")
        with open(cls.html_path, "w", encoding="utf-8") as f:
            f.write(cls._page_html())
        cls._pw = sync_playwright().start()
        try:
            cls._browser = cls._pw.chromium.launch()
        except Exception as e:  # browser binary missing
            cls._pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()
            os.unlink(cls.html_path)
            raise unittest.SkipTest("chromium not available: %s" % e)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.unlink(cls.html_path)

    @classmethod
    def _page_html(cls):
        return (
            "<!doctype html><html><head><meta charset='utf-8'></head><body>\n"
            "<script src='lightweight-charts.js'></script>\n"
            "<script>\n"
            "window.__errors=[];\n"
            "window.addEventListener('error',function(e){window.__errors.push(String(e.message||(e.error&&e.error.message)||e));});\n"
            "function frame(){return new Promise(function(r){requestAnimationFrame(function(){requestAnimationFrame(r);});});}\n"
            + cls.sanitizer + "\n"
            "function mk(){var h=document.createElement('div');h.style.cssText='width:600px;height:300px;';document.body.appendChild(h);"
            "var c=LightweightCharts.createChart(h,{width:600,height:300});return{chart:c,s:c.addCandlestickSeries(),host:h};}\n"
            "function mapRaw(rows){return rows.map(function(p){return {time:p.time,open:p.open,high:p.high,low:p.low,close:p.close};});}\n"
            "function mapFixed(rows){return rows.map(function(p){var s=_sanitizeOHLCV(p);if(!s)return null;"
            "return {time:p.time,open:s.open,high:s.high,low:s.low,close:s.close};}).filter(function(x){return x!==null;});}\n"
            "async function paint(bars){var o=mk();try{o.s.setData(bars);}catch(e){window.__errors.push('setData: '+(e&&e.message||e));}"
            "await frame();await frame();try{o.chart.remove();}catch(e){}o.host.remove();}\n"
            "window.__run=async function(mode,rows){window.__errors=[];var bars=(mode==='raw'?mapRaw:mapFixed)(rows);"
            "try{await paint(bars);}catch(e){window.__errors.push(String(e&&e.message||e));}return window.__errors.slice();};\n"
            "</script></body></html>\n"
        )

    def _page(self):
        pg = self._browser.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"http://127.0.0.1:{self.port}/_p12_test.html")
        return pg, errs

    def test_control_raw_null_open_close_throws_value_is_null(self):
        rows = [
            {"time": 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"time": 1300, "open": None, "high": 2, "low": 0.5, "close": 1.5},   # null OPEN
            {"time": 1600, "open": 1, "high": 2, "low": 0.5, "close": None},     # null CLOSE
        ]
        pg, errs = self._page()
        res = pg.evaluate("(a) => window.__run('raw', a)", rows)
        pg.close()
        joined = " ".join(res) + " " + " ".join(errs)
        self.assertIn("Value is null", joined,
                      "control: raw null open/close must reproduce the LWC error (mechanism premise)")

    def test_sanitizer_prevents_the_error(self):
        rows = [
            {"time": 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5},       # valid
            {"time": 1300, "open": None, "high": 2, "low": 0.5, "close": 1.5},     # null open
            {"time": 1600, "open": 1, "high": 2, "low": 0.5, "close": None},       # null close
            {"time": 1900, "open": 1, "high": 2, "low": 0.5},                      # missing close
            {"time": 2200, "open": 0, "high": 2, "low": 0.5, "close": 1.5},        # zero open
            {"time": None, "open": 1, "high": 2, "low": 0.5, "close": 1.5},        # null time
            {"time": 2500, "open": 1, "high": 2, "low": 0.5, "close": 1.5},        # valid
        ]
        pg, errs = self._page()
        res = pg.evaluate("(a) => window.__run('fixed', a)", rows)
        pg.close()
        self.assertEqual(res, [], "sanitized bars must not raise any LWC error: %s" % res)
        self.assertEqual(errs, [])

    def test_valid_data_still_renders(self):
        rows = [{"time": 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5},
                {"time": 1300, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]
        pg, errs = self._page()
        res = pg.evaluate("(a) => window.__run('fixed', a)", rows)
        pg.close()
        self.assertEqual(res, [])
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
