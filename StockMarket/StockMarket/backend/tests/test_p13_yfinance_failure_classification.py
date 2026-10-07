"""P1.3 — yfinance failure-classification regression (data-integrity).

`_sync_yfinance_inactive_symbols()` persists `get_inactive_symbols()` to
`stock_metadata.is_active = False`, which hides a ticker from /api/all-stocks and
the screener and has NO un-deactivate path. `_mark_failed(NOT_FOUND)` used to add
to that set, but NOT_FOUND also fires for transient conditions ("no data found
for this date range", "ticker not found in batch result"), so the startup
pre-population hid valid, data-bearing tickers.

These tests lock the corrected contract: only a genuine DELISTED signal may mark
a symbol permanently inactive; every other class (including NOT_FOUND) still
enters the in-process failure cache (so the ticker is skipped for its retry
interval and creates no extra yfinance load) but must never reach the DB-facing
inactive set.
"""
import unittest

from yfinance_downloader import YFinanceDownloader, YFErrorClass


class FailureClassificationTests(unittest.TestCase):
    def setUp(self):
        self.dl = YFinanceDownloader()

    def test_not_found_is_cached_but_not_persisted_inactive(self):
        self.dl._mark_failed("NOTFOUNDX", YFErrorClass.NOT_FOUND)
        # in-process skip preserved (no extra load)
        self.assertTrue(self.dl.is_failed("NOTFOUNDX"))
        # ...but it must never be queued for the destructive DB write
        self.assertNotIn("NOTFOUNDX", self.dl.get_inactive_symbols())

    def test_delisted_is_persisted_inactive(self):
        self.dl._mark_failed("DELISTEDX", YFErrorClass.DELISTED)
        self.assertTrue(self.dl.is_failed("DELISTEDX"))
        self.assertIn("DELISTEDX", self.dl.get_inactive_symbols())

    def test_no_other_class_is_persisted_inactive(self):
        others = [
            YFErrorClass.EMPTY, YFErrorClass.NETWORK, YFErrorClass.RATE_LIMIT,
            YFErrorClass.TIMEOUT, YFErrorClass.UNKNOWN, YFErrorClass.NOT_FOUND,
        ]
        for i, cls in enumerate(others):
            name = f"CLASSX{i}"
            self.dl._mark_failed(name, cls)
            self.assertTrue(self.dl.is_failed(name), cls)
            self.assertNotIn(name, self.dl.get_inactive_symbols(), cls)

    def test_startup_prepopulation_no_longer_deactivates(self):
        # Reproduces the startup heuristic's marking pattern: many tickers with
        # no recent 5m candles => NOT_FOUND. None may reach the inactive set.
        stale = [f"HIDDEN{i}" for i in range(25)]
        for t in stale:
            self.dl._mark_failed(t, YFErrorClass.NOT_FOUND)
        self.assertEqual(self.dl.get_inactive_symbols(), {})
        for t in stale:
            self.assertTrue(self.dl.is_failed(t))

    def test_clear_inactive_symbols_only_clears_delisted(self):
        self.dl._mark_failed("D1", YFErrorClass.DELISTED)
        self.dl._mark_failed("N1", YFErrorClass.NOT_FOUND)
        self.assertEqual(set(self.dl.get_inactive_symbols().keys()), {"D1"})
        self.dl.clear_inactive_symbols()
        self.assertEqual(self.dl.get_inactive_symbols(), {})


if __name__ == "__main__":
    unittest.main()
