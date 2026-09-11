import unittest
from migration.identity import resolve_ticker_universe, TickerIdentity, ShadowedListing


class TestResolveTickerUniverse(unittest.TestCase):
    def test_single_nse_listing(self):
        identities, shadowed = resolve_ticker_universe([
            {"ticker": "RELIANCE", "exchange": "NSE"},
        ])
        self.assertEqual(len(identities), 1)
        self.assertEqual(identities[0], TickerIdentity(ticker="RELIANCE", exchange="NSE", is_dual_listed=False, shadowed_exchange=None))
        self.assertEqual(shadowed, [])

    def test_single_bse_only_listing_is_not_dropped(self):
        """A symbol active ONLY on BSE (no NSE counterpart) must still resolve
        -- not be silently excluded just because NSE is the priority exchange."""
        identities, shadowed = resolve_ticker_universe([
            {"ticker": "SOMEBSESTOCK", "exchange": "BSE"},
        ])
        self.assertEqual(len(identities), 1)
        self.assertEqual(identities[0].ticker, "SOMEBSESTOCK")
        self.assertEqual(identities[0].exchange, "BSE")
        self.assertFalse(identities[0].is_dual_listed)
        self.assertEqual(shadowed, [])

    def test_dual_listed_symbol_resolves_to_nse_and_reports_bse_as_shadowed(self):
        """The exact collision scenario from the audit: same symbol active on
        both NSE and BSE. Must resolve to exactly one canonical identity
        (NSE, matching angelone_service.get_token's existing default), and
        the BSE listing must be reported, not silently discarded."""
        identities, shadowed = resolve_ticker_universe([
            {"ticker": "DUALSTOCK", "exchange": "NSE"},
            {"ticker": "DUALSTOCK", "exchange": "BSE"},
        ])
        self.assertEqual(len(identities), 1)
        identity = identities[0]
        self.assertEqual(identity.ticker, "DUALSTOCK")
        self.assertEqual(identity.exchange, "NSE")
        self.assertTrue(identity.is_dual_listed)
        self.assertEqual(identity.shadowed_exchange, "BSE")

        self.assertEqual(len(shadowed), 1)
        self.assertEqual(shadowed[0], ShadowedListing(ticker="DUALSTOCK", excluded_exchange="BSE", canonical_exchange="NSE"))

    def test_dual_listed_symbol_order_independent(self):
        """Input row order must not change which exchange wins -- NSE priority
        is a fixed rule, not "whichever came first in the query result"."""
        a, _ = resolve_ticker_universe([
            {"ticker": "X", "exchange": "BSE"},
            {"ticker": "X", "exchange": "NSE"},
        ])
        b, _ = resolve_ticker_universe([
            {"ticker": "X", "exchange": "NSE"},
            {"ticker": "X", "exchange": "BSE"},
        ])
        self.assertEqual(a[0].exchange, "NSE")
        self.assertEqual(b[0].exchange, "NSE")

    def test_different_tokens_stay_attached_to_the_resolved_exchange(self):
        """This module doesn't resolve Angel One tokens itself (that's
        angelone_service.get_token's job) -- but it must hand the correct
        (ticker, exchange) pair downstream so THAT lookup uses the right
        exchange, not always NSE. Simulate two different tokens per exchange
        and confirm the resolved identity's exchange is what a caller would
        pass into get_token(ticker, exchange=...)."""
        mock_tokens = {
            ("RELIANCE", "NSE"): "2885",
            ("RELIANCE", "BSE"): "500325",
        }
        identities, _ = resolve_ticker_universe([
            {"ticker": "RELIANCE", "exchange": "NSE"},
            {"ticker": "RELIANCE", "exchange": "BSE"},
        ])
        resolved_token = mock_tokens[(identities[0].ticker, identities[0].exchange)]
        self.assertEqual(identities[0].exchange, "NSE")
        self.assertEqual(resolved_token, "2885")  # NOT the BSE token 500325

    def test_multiple_independent_tickers(self):
        identities, shadowed = resolve_ticker_universe([
            {"ticker": "A", "exchange": "NSE"},
            {"ticker": "B", "exchange": "BSE"},
            {"ticker": "C", "exchange": "NSE"},
            {"ticker": "C", "exchange": "BSE"},
        ])
        by_ticker = {i.ticker: i for i in identities}
        self.assertEqual(len(identities), 3)
        self.assertEqual(by_ticker["A"].exchange, "NSE")
        self.assertEqual(by_ticker["B"].exchange, "BSE")
        self.assertEqual(by_ticker["C"].exchange, "NSE")
        self.assertEqual(len(shadowed), 1)
        self.assertEqual(shadowed[0].ticker, "C")

    def test_empty_input(self):
        identities, shadowed = resolve_ticker_universe([])
        self.assertEqual(identities, [])
        self.assertEqual(shadowed, [])

    def test_rows_with_missing_ticker_or_exchange_are_skipped(self):
        identities, shadowed = resolve_ticker_universe([
            {"ticker": "", "exchange": "NSE"},
            {"ticker": "VALID", "exchange": ""},
            {"ticker": "VALID2", "exchange": "NSE"},
        ])
        self.assertEqual(len(identities), 1)
        self.assertEqual(identities[0].ticker, "VALID2")

    def test_result_is_deterministic_across_repeated_calls(self):
        """Idempotency: resolving the same input twice must produce identical
        output -- the migration planner must never propose different work on
        successive runs against unchanged data."""
        rows = [
            {"ticker": "RELIANCE", "exchange": "NSE"},
            {"ticker": "RELIANCE", "exchange": "BSE"},
            {"ticker": "TCS", "exchange": "NSE"},
        ]
        first = resolve_ticker_universe(rows)
        second = resolve_ticker_universe(rows)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
