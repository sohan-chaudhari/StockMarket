"""
Proves the exchange resolved by migration/identity.py actually reaches the
Angel One call, end to end through AngelOneFetchManager -- not just that the
pure resolver picks the right exchange in isolation. Uses a mocked
historical_service (no real Angel One call), per the "mocked/fake Angel One
responses in tests" allowance -- this test makes zero network calls.
"""
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

from migration.fetch_manager import AngelOneFetchManager
from migration.config import MigrationConfig
from migration.identity import resolve_ticker_universe


class TestFetchUsesResolvedExchange(unittest.TestCase):
    def setUp(self):
        self.cfg = MigrationConfig(requests_per_second=1000)  # fast for tests
        self.mgr = AngelOneFetchManager(self.cfg)

    def test_fetch_passes_resolved_exchange_to_historical_service(self):
        identities, _ = resolve_ticker_universe([
            {"ticker": "SOMEBSESTOCK", "exchange": "BSE"},
        ])
        identity = identities[0]
        self.assertEqual(identity.exchange, "BSE")

        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.return_value = [
                {"timestamp": date(2026, 1, 1), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
            ]
            success, candles, err, retryable = self.mgr.fetch(
                identity.ticker, "ONE_DAY", date(2026, 1, 1), date(2026, 1, 2), exchange=identity.exchange,
            )

        self.assertTrue(success)
        call_kwargs = mock_hs.get_historical_candles.call_args.kwargs
        self.assertEqual(call_kwargs["exchange"], "BSE", "must fetch against BSE, not the NSE default")

    def test_fetch_defaults_to_nse_when_no_exchange_given(self):
        """Confirms the existing default (matches angelone_service.get_token's
        own default) is still NSE when a caller doesn't specify -- this is
        the convention migration/identity.py's NSE-priority rule is built on,
        not a new one invented separately."""
        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.return_value = []
            self.mgr.fetch("RELIANCE", "ONE_DAY", date(2026, 1, 1), date(2026, 1, 2))

        call_kwargs = mock_hs.get_historical_candles.call_args.kwargs
        self.assertEqual(call_kwargs["exchange"], "NSE")

    def test_dual_listed_symbol_each_resolve_call_hits_its_own_exchange(self):
        """The full scenario from item D: same symbol string, two different
        exchanges, must not mix. Two separate fetch() calls for the same
        ticker string but different resolved exchanges must each reach
        historical_service with their own exchange, never the other's."""
        with patch("migration.fetch_manager.historical_service") as mock_hs:
            mock_hs.is_logged_in = True
            mock_hs.get_historical_candles.return_value = []

            self.mgr.fetch("DUALSTOCK", "ONE_DAY", date(2026, 1, 1), date(2026, 1, 2), exchange="NSE")
            first_call_exchange = mock_hs.get_historical_candles.call_args.kwargs["exchange"]

            self.mgr.fetch("DUALSTOCK", "ONE_DAY", date(2026, 1, 1), date(2026, 1, 2), exchange="BSE")
            second_call_exchange = mock_hs.get_historical_candles.call_args.kwargs["exchange"]

        self.assertEqual(first_call_exchange, "NSE")
        self.assertEqual(second_call_exchange, "BSE")
        self.assertNotEqual(first_call_exchange, second_call_exchange)


if __name__ == "__main__":
    unittest.main()
