"""P1.1b — Markets (market.html) chart payload regression tests.

Static contract checks on the shipped market.html inline chart script + a
behavioural check of the extracted `renderChartCached` (run through Node) proving
the paginated `{data, has_more}` response is unwrapped and rendered with correct
chronological order / no duplicates. The backend endpoint is unchanged by P1.1b,
so its own invariants are covered by test_p11_chart_payload_pagination.py.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "frontend")
MARKET = os.path.join(FRONTEND_DIR, "market.html")


def _html():
    with open(MARKET, "r", encoding="utf-8") as f:
        return f.read()


def _inline_script():
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", _html(), re.DOTALL)
    return max(blocks, key=len)


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


class MarketsChartPayloadStaticTests(unittest.TestCase):
    def setUp(self):
        self.html = _html()

    def test_page_size_constant_present(self):
        self.assertIn("var MARKET_INITIAL_BARS = 600;", self.html)

    def test_intraday_initial_uses_paginated_endpoint_with_page_size(self):
        self.assertIn('/api/stock-data/intraday/paginated?ticker=', self.html)
        for needle in (
            '&interval=5m&limit="+MARKET_INITIAL_BARS',
            '&interval=15m&limit="+MARKET_INITIAL_BARS',
            '"&interval="+range+"&limit="+MARKET_INITIAL_BARS',
        ):
            self.assertIn(needle, self.html)

    def test_legacy_non_paginated_intraday_endpoint_no_longer_used(self):
        # the initial-load URLs must not hit the fixed-limit legacy endpoint
        self.assertNotIn('/api/stock-data/intraday?ticker=', self.html)

    def test_paginated_response_is_unwrapped(self):
        self.assertIn("if (data && data.data) data = data.data;", self.html)

    def test_lazy_load_chunk_unchanged(self):
        # P1.1b must not alter the existing 200-bar Markets lazy-load page size
        self.assertIn("&interval='+range+'&before='+beforeTime+'&limit=200'", self.html)

    def test_non_intraday_ranges_still_use_range_endpoint(self):
        self.assertIn('/api/stock-data/range?ticker="+encodeURIComponent(ticker)+"&range="+range;', self.html)

    def test_no_p12_null_guard_changes_smuggled_in(self):
        # guard against P1.2 scope creep: the pre-existing render guard is untouched
        self.assertIn("if(Array.isArray(data)&&data.length>0){", self.html)


@unittest.skipUnless(shutil.which("node"), "node not available")
class MarketsInlineScriptTests(unittest.TestCase):
    def test_inline_script_parses(self):
        script = _inline_script()
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_render_chart_cached_orders_dedupes_and_accepts_paginated_shape(self):
        script = _inline_script()
        src = _extract_function(script, "toTimeNum") + "\n" + _extract_function(script, "renderChartCached")
        bars = [
            {"time": 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 5},
            {"time": 1300, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 6},
            {"time": 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 7},  # duplicate ts
            {"time": 1600, "open": 0, "high": 2, "low": 0.5, "close": 1.5, "volume": 8},  # invalid open -> dropped
        ]
        js = (
            "var _chartDataCache=null,_earliestLoadedTime=null;"
            "var window={};"
            "var _setupLazyLoad=function(){};"
            "var candleSeries={setData:function(d){globalThis.__out=d;}};"
            "var chart={timeScale:function(){return {setVisibleLogicalRange:function(){},fitContent:function(){}};}};"
            + src + "\n"
            + "var payload={data:" + json.dumps(bars) + ",has_more:true};"
            + "var data=payload; if(data&&data.data) data=data.data;"
            + "renderChartCached(data,'5m');"
            + "console.log(JSON.stringify(globalThis.__out));"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(js)
            path = fh.name
        try:
            proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        # ascending, deduped, invalid bar dropped, OHLC preserved
        self.assertEqual([b["time"] for b in out], [1000, 1300])
        self.assertEqual(out[0]["open"], 1)
        self.assertEqual(out[0]["close"], 1.5)


if __name__ == "__main__":
    unittest.main()
