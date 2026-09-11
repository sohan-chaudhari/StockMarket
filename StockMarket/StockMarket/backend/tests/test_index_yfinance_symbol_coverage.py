"""Proves every dashboard index has a real yfinance symbol mapping in BOTH
places this app keeps one, so the yfinance gap-fill/recovery fallback never
silently no-ops for an index the way it did for FINNIFTY/MIDCAP/SMALLCAP.

CANDLE-GAP INVESTIGATION: the dashboard's mini-chart (dashboard.js's
initNiftyChart/loadChartData) lets the user switch between all 6 indices
(NIFTY/SENSEX/BANKNIFTY/FINNIFTY/MIDCAP/SMALLCAP) and always fetches via
/api/stock-data/intraday/paginated, whose auto-gap-fill
(perform_on_demand_backfill in main.py) tries AngelOne first and falls back
to yfinance via _yfinance_ticker()/YFINANCE_INDEX_MAP when AngelOne returns
nothing. Two copies of this same index->yfinance-symbol map exist
(main.py's module-level one, and a separate local one inside
recovery_service.py's REST-recovery fallback) and BOTH had previously been
found missing FINNIFTY/MIDCAP/SMALLCAP -- without a real mapping,
_yfinance_ticker() falls through to f"{ticker}.NS" (e.g. "FINNIFTY.NS"),
which is not a real yfinance symbol for an index, so the fallback silently
returns nothing and any gap for these 3 tickers never gets closed. This
locks in the fix (main.py's copy) and guards recovery_service.py's copy
from regressing the same way again.

Source-level check (not a live yfinance call) -- proves the mapping exists
and matches the known-correct symbols, not that yfinance itself is
reachable from this environment.
"""
import ast
import os
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PY_PATH = os.path.join(BACKEND_DIR, "main.py")
RECOVERY_SERVICE_PATH = os.path.join(BACKEND_DIR, "recovery_service.py")

# The 6 indices the dashboard's ticker-switch minichart actually offers
# (dashboard.js: TICKER_STRIP_INDICES / reqTickers).
DASHBOARD_INDICES = {"NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCAP", "SMALLCAP"}

# Known-correct symbols -- recovery_service.py's copy was fixed first and is
# the reference; main.py's copy must agree with it exactly (a real, listed
# yfinance ticker per index is not a matter of style, there's one right
# answer per index).
EXPECTED_SYMBOLS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCAP": "^NSEMDCP50",
    "SMALLCAP": "^NSESCP250",
}


def _find_dict_literal_named(tree, name):
    """Returns the literal dict for the first module- or function-level
    `name = {...}` assignment found anywhere in the tree, or None."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
                and isinstance(node.value, ast.Dict)):
            return ast.literal_eval(node.value)
    return None


class TestMainPyIndexMapCoverage(unittest.TestCase):
    def setUp(self):
        with open(MAIN_PY_PATH, "r", encoding="utf-8") as f:
            self.tree = ast.parse(f.read(), filename=MAIN_PY_PATH)
        self.index_map = _find_dict_literal_named(self.tree, "YFINANCE_INDEX_MAP")

    def test_map_exists(self):
        self.assertIsNotNone(self.index_map, "YFINANCE_INDEX_MAP not found in main.py")

    def test_every_dashboard_index_is_covered(self):
        missing = DASHBOARD_INDICES - set(self.index_map.keys())
        self.assertEqual(missing, set(),
                          f"main.py's YFINANCE_INDEX_MAP is missing dashboard indices: {sorted(missing)}")

    def test_symbols_match_the_known_correct_values(self):
        for ticker, expected in EXPECTED_SYMBOLS.items():
            self.assertEqual(self.index_map.get(ticker), expected,
                              f"{ticker} -> {self.index_map.get(ticker)!r}, expected {expected!r}")


class TestRecoveryServiceIndexMapCoverage(unittest.TestCase):
    """Guards the OTHER copy of this map -- already fixed in an earlier
    phase, this just prevents it from silently regressing again."""

    def setUp(self):
        with open(RECOVERY_SERVICE_PATH, "r", encoding="utf-8") as f:
            self.tree = ast.parse(f.read(), filename=RECOVERY_SERVICE_PATH)
        self.index_map = _find_dict_literal_named(self.tree, "YFINANCE_INDEX_MAP")

    def test_map_exists(self):
        self.assertIsNotNone(self.index_map, "YFINANCE_INDEX_MAP not found in recovery_service.py")

    def test_every_dashboard_index_is_covered(self):
        missing = DASHBOARD_INDICES - set(self.index_map.keys())
        self.assertEqual(missing, set(),
                          f"recovery_service.py's YFINANCE_INDEX_MAP is missing dashboard indices: {sorted(missing)}")

    def test_symbols_match_the_known_correct_values(self):
        for ticker, expected in EXPECTED_SYMBOLS.items():
            self.assertEqual(self.index_map.get(ticker), expected,
                              f"{ticker} -> {self.index_map.get(ticker)!r}, expected {expected!r}")


if __name__ == "__main__":
    unittest.main()
