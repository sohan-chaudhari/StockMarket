"""P1.1 — Chart payload/pagination regression tests (frontend).

Static contract checks on the shipped dashboard.js + a behavioural check of the
extracted `_buildIntradayDisplay` remap run through Node (no browser needed).
Verifies the intraday initial/lazy page size, that lazy-load is enabled and uses
the real "before" epoch, that the sequential index base is rebuilt on prepend,
and that every page loads the new dashboard.js asset version.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "frontend")
DASH = os.path.join(FRONTEND_DIR, "dashboard.js")

HTML_PAGES = [
    "home.html", "index.html", "stock.html", "chart.html", "tv-chart.html",
    "index_chart.html", "stock_restructured.html", "stock_rebuilt.html",
]


def _src():
    with open(DASH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(src, name):
    """Return the full source of `function <name>(...) { ... }` (brace matched)."""
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


class ChartPayloadFrontendStaticTests(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_page_size_constant_present(self):
        self.assertIn("var INTRADAY_PAGE_BARS = 600;", self.src)

    def test_initial_intraday_request_uses_page_size(self):
        self.assertIn("&interval=' + range + '&limit=' + INTRADAY_PAGE_BARS", self.src)

    def test_intraday_requests_no_longer_use_15000(self):
        self.assertNotIn("&interval=' + range + '&limit=15000", self.src)
        # and no intraday/paginated line carries a literal 15000
        for line in self.src.splitlines():
            if "intraday/paginated" in line and "url = " in line:
                self.assertNotIn("15000", line, line.strip())

    def test_intraday_lazy_load_enabled(self):
        self.assertNotIn("if (window._intradayBarTimes) return;", self.src)

    def test_fetch_more_uses_real_earliest_epoch_for_intraday(self):
        self.assertIn("window._intradayEarliestEpoch", self.src)
        self.assertIn("beforeEpochIntra", self.src)

    def test_shared_remap_and_prepend_helpers_exist_and_are_used(self):
        self.assertIn("function _buildIntradayDisplay(", self.src)
        self.assertIn("function _prependIntradayAndRender(", self.src)
        self.assertIn("displayData = _buildIntradayDisplay(unique, _BAR_SECS);", self.src)
        self.assertIn("_prependIntradayAndRender(incoming, resp.has_more);", self.src)

    def test_all_pages_cache_bust_dashboard_js(self):
        for page in HTML_PAGES:
            p = os.path.join(FRONTEND_DIR, page)
            with open(p, "r", encoding="utf-8") as f:
                body = f.read()
            self.assertIn("dashboard.js?v=1066", body, f"{page} must reference the new dashboard.js version")
            self.assertNotIn("dashboard.js?v=1065", body, f"{page} still references the old dashboard.js version")


@unittest.skipUnless(shutil.which("node"), "node not available")
class IntradayDisplayBehaviourTests(unittest.TestCase):
    """Runs the actual shipped `_buildIntradayDisplay` in Node."""

    def _run(self, real_bars):
        fn = _extract_function(_src(), "_buildIntradayDisplay")
        script = (
            "global.window = {};\n"
            + fn + "\n"
            + "const bars = " + json.dumps(real_bars) + ";\n"
            + "const out = _buildIntradayDisplay(bars, 300);\n"
            + "console.log(JSON.stringify({out: out, times: window._intradayBarTimes,"
            + " secs: window._intradayBarSecs, dayMap: window._intradayDayFirstBarMap,"
            + " seq: window._intradayBarSeqMap}));\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_sequential_times_and_lookup_tables(self):
        d1 = 1772423100  # 2026-03-02 09:15 IST (03:45 UTC)
        bars = [
            {"time": d1, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1},
            {"time": d1 + 300, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 2},
            {"time": d1 + 600, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 3},
            {"time": d1 + 86400, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 4},
            {"time": d1 + 86400 + 300, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 5},
        ]
        res = self._run(bars)
        out = res["out"]
        # display time is index * barSecs
        self.assertEqual([b["time"] for b in out], [0, 300, 600, 900, 1200])
        # real epochs preserved in the parallel lookup table
        self.assertEqual(res["times"], [b["time"] for b in bars])
        self.assertEqual(res["secs"], 300)
        # seqMap invalidated so the forming-candle mapper rebuilds on the new base
        self.assertIsNone(res["seq"])
        # OHLCV values carried through unchanged
        for i, b in enumerate(bars):
            self.assertEqual(out[i]["open"], b["open"])
            self.assertEqual(out[i]["close"], b["close"])
            self.assertEqual(out[i]["volume"], b["volume"])
        # day-first-bar index map drives session separators
        self.assertEqual(res["dayMap"]["2026-03-02"], 0)
        self.assertEqual(res["dayMap"]["2026-03-03"], 3)

    def test_prepend_shifts_index_base_and_keeps_order(self):
        # A page of older bars prepended must shift every existing index uniformly.
        d0 = 1772336700  # one day earlier
        older = [{"time": d0 + i * 300, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": i} for i in range(2)]
        cur = [{"time": d0 + 86400 + i * 300, "open": 3, "high": 4, "low": 2.5, "close": 3.5, "volume": i} for i in range(3)]
        res = self._run(older + cur)
        out = res["out"]
        self.assertEqual([b["time"] for b in out], [0, 300, 600, 900, 1200])
        self.assertEqual(res["times"], [b["time"] for b in (older + cur)])
        # the first bar of the previously-current day moved from index 0 to index 2
        self.assertEqual(res["dayMap"]["2026-03-02"], 2)


if __name__ == "__main__":
    unittest.main()
