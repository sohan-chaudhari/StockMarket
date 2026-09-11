"""Regression tests for the BSE-exchange data-corruption bug found while
investigating the daily-source (stock_data vs candles) switch.

/api/stock-data/range's fallback fetches (yfinance, Angel One) hardcoded
every ticker's exchange as NSE. For a BSE-only ticker, this silently
queries the wrong exchange -- yfinance's `.NS` suffix or Angel One's NSE
token lookup can resolve to a *different real instrument* under the same
symbol rather than a clean "not found". Confirmed concretely: ticker META
("String Metaverse Limited", BSE-only, real price ~Rs 6-7) had a
stock_data closing price of ~Rs 13,500 -- over 1800x too high -- fed by
this exact fallback path.

These tests exercise the two new pieces directly (no app/DB required):
_resolve_ticker_exchange() and the now exchange-aware _yfinance_ticker().
"""
import unittest
from unittest.mock import MagicMock

from main import _yfinance_ticker, _resolve_ticker_exchange


class TestYfinanceTickerIsExchangeAware(unittest.TestCase):
    def test_defaults_to_ns_suffix_when_exchange_unspecified(self):
        # Every existing call site that doesn't pass exchange must keep
        # today's exact behavior.
        self.assertEqual(_yfinance_ticker("RELIANCE"), "RELIANCE.NS")

    def test_uses_bo_suffix_for_bse(self):
        self.assertEqual(_yfinance_ticker("META", "BSE"), "META.BO")

    def test_uses_ns_suffix_explicitly_for_nse(self):
        self.assertEqual(_yfinance_ticker("RELIANCE", "NSE"), "RELIANCE.NS")

    def test_index_map_ignores_exchange(self):
        self.assertEqual(_yfinance_ticker("NIFTY", "BSE"), "^NSEI")
        self.assertEqual(_yfinance_ticker("SENSEX", "NSE"), "^BSESN")


class TestResolveTickerExchange(unittest.TestCase):
    def _mock_db(self, rows):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = rows
        return db

    def test_bse_only_ticker_resolves_to_bse(self):
        db = self._mock_db([("BSE",)])
        self.assertEqual(_resolve_ticker_exchange(db, "META"), "BSE")

    def test_nse_only_ticker_resolves_to_nse(self):
        db = self._mock_db([("NSE",)])
        self.assertEqual(_resolve_ticker_exchange(db, "RELIANCE"), "NSE")

    def test_dual_listed_prefers_nse(self):
        db = self._mock_db([("NSE",), ("BSE",)])
        self.assertEqual(_resolve_ticker_exchange(db, "RELIANCE"), "NSE")

    def test_no_metadata_defaults_to_nse(self):
        # No behavior change for a ticker with no metadata row at all --
        # matches what every caller assumed before this function existed.
        db = self._mock_db([])
        self.assertEqual(_resolve_ticker_exchange(db, "UNKNOWN"), "NSE")


if __name__ == "__main__":
    unittest.main()
