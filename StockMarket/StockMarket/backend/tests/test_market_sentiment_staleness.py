"""Proves /api/scanx/news/market-sentiment (the dashboard's "AI insights"
sentiment gauge) passes the real market-open state into
fetch_batch_live_data(), so AngelOne tick staleness detection is actually
active for it.

AI-INSIGHTS ACCURACY CHECK: fetch_batch_live_data() only runs its >30s tick
staleness check (main.py, `if tick_data and market_open:`) when its
market_open argument is True -- with the default (False), a stuck/stale
AngelOne tick for NIFTY/SENSEX/BANKNIFTY during real trading hours would be
silently accepted as current forever, since the caller never opted into
staleness detection. The sentiment endpoint was calling
fetch_batch_live_data(['NIFTY','SENSEX','BANKNIFTY']) with no market_open
argument at all (always False), unlike /api/live-prices (the main dashboard
price endpoint, main.py ~line 2070), which already correctly passes
market_open=is_market_open_now(). Fixed to do the same.

Source-level check (parses main.py's AST) -- proves the fix is present in
the source without needing to boot the real app (main.py has expensive
import-time side effects; see test_sentry_config.py for why every test
touching main.py-adjacent config uses this pattern instead)."""
import ast
import os
import unittest

MAIN_PY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


class TestMarketSentimentPassesRealMarketOpenState(unittest.TestCase):
    def setUp(self):
        with open(MAIN_PY_PATH, "r", encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=MAIN_PY_PATH)
        self.func = _find_function(self.tree, "proxy_market_sentiment")

    def test_endpoint_function_exists(self):
        self.assertIsNotNone(self.func, "proxy_market_sentiment not found in main.py")

    def test_endpoint_is_still_registered_at_the_expected_route(self):
        self.assertIn('@app.get("/api/scanx/news/market-sentiment")', self.source)

    def test_fetch_batch_live_data_call_passes_market_open(self):
        """The specific bug: this call previously had no market_open kwarg
        at all, silently defaulting to False regardless of real market
        state."""
        func_src = ast.unparse(self.func)
        self.assertIn("fetch_batch_live_data(", func_src)
        for node in ast.walk(self.func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "fetch_batch_live_data"):
                kwarg_names = [kw.arg for kw in node.keywords]
                self.assertIn("market_open", kwarg_names,
                               "fetch_batch_live_data() call in proxy_market_sentiment "
                               "must pass market_open explicitly")
                return
        self.fail("No fetch_batch_live_data(...) call found inside proxy_market_sentiment")

    def test_market_open_value_is_the_real_market_state_not_a_literal(self):
        """Must be wired to the real is_market_open_now() check, not
        hardcoded True/False (hardcoding True would over-trigger staleness
        rejection outside market hours; hardcoding False reintroduces
        exactly this bug)."""
        for node in ast.walk(self.func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "fetch_batch_live_data"):
                for kw in node.keywords:
                    if kw.arg == "market_open":
                        value_src = ast.unparse(kw.value)
                        self.assertEqual(value_src, "is_market_open_now()")
                        return
        self.fail("market_open kwarg not found")


if __name__ == "__main__":
    unittest.main()
