import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from recovery_service import RecoveryService


class TestRecoveryService(unittest.TestCase):
    def setUp(self):
        self.db_factory = MagicMock()
        self.historical_service = MagicMock()
        self.yf_downloader = MagicMock()
        self.service = RecoveryService(
            db_session_factory=self.db_factory,
            historical_service=self.historical_service,
            yf_downloader=self.yf_downloader,
        )

    def test_needs_recovery_initial(self):
        self.assertTrue(self.service.needs_recovery("RELIANCE"))

    def test_needs_recovery_after_mark(self):
        self.service.mark_recovered("RELIANCE")
        self.assertFalse(self.service.needs_recovery("RELIANCE"))

    def test_mark_recovered_twice(self):
        self.service.mark_recovered("RELIANCE")
        self.service.mark_recovered("RELIANCE")
        self.assertFalse(self.service.needs_recovery("RELIANCE"))

    def test_reset_clears_all(self):
        self.service.mark_recovered("R1")
        self.service.mark_recovered("R2")
        self.service.reset()
        self.assertTrue(self.service.needs_recovery("R1"))
        self.assertTrue(self.service.needs_recovery("R2"))

    def test_mark_needs_recovery_re_arms_a_recovered_ticker(self):
        # RT-05: without this, a second WS gap for a ticker already
        # recovered once this process lifetime would silently no-op --
        # needs_recovery() stays False forever after the first recovery.
        self.service.mark_recovered("RELIANCE")
        self.assertFalse(self.service.needs_recovery("RELIANCE"))
        self.service.mark_needs_recovery("RELIANCE")
        self.assertTrue(self.service.needs_recovery("RELIANCE"))

    def test_mark_needs_recovery_on_unrecovered_ticker_is_a_noop(self):
        self.service.mark_needs_recovery("NEVER_RECOVERED")
        self.assertTrue(self.service.needs_recovery("NEVER_RECOVERED"))

    def test_second_gap_same_day_triggers_a_real_recovery(self):
        # End-to-end: a second gap for the same ticker later the same day
        # must actually re-fetch, not silently return True from the
        # one-shot guard like it did before RT-05.
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        self.db_factory.return_value = mock_db
        self.historical_service.is_logged_in = True
        self.historical_service.get_historical_candles.return_value = [
            {"timestamp": datetime(2026, 7, 1, 10, 0, 0), "open": 100, "high": 105,
             "low": 95, "close": 102, "volume": 1000}
        ]

        self.service.recover_ticker("RELIANCE", MagicMock())
        self.assertEqual(self.historical_service.get_historical_candles.call_count, 1)

        # Second gap, same process, same ticker, later the same day.
        self.service.mark_needs_recovery("RELIANCE")
        self.service.recover_ticker("RELIANCE", MagicMock())
        self.assertEqual(self.historical_service.get_historical_candles.call_count, 2)

    def test_recover_ticker_noop_if_already_recovered(self):
        self.service.mark_recovered("RELIANCE")
        result = self.service.recover_ticker("RELIANCE", None)
        self.assertTrue(result)

    def test_recover_ticker_recovery_flow(self):
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.first.return_value = None
        mock_db.query.return_value = mock_query
        self.db_factory.return_value = mock_db

        self.historical_service.is_logged_in = True
        self.historical_service.get_historical_candles.return_value = [
            {"timestamp": datetime(2026, 7, 1, 10, 0, 0), "open": 100, "high": 105,
             "low": 95, "close": 102, "volume": 1000}
        ]

        mock_builder = MagicMock()
        result = self.service.recover_ticker("RELIANCE", mock_builder)
        self.assertTrue(result)
        mock_builder.init_ticker_from_last_candle.assert_called_once()

    def test_recover_ticker_no_candles(self):
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.first.return_value = None
        mock_db.query.return_value = mock_query
        self.db_factory.return_value = mock_db

        self.historical_service.is_logged_in = True
        self.historical_service.get_historical_candles.return_value = []

        result = self.service.recover_ticker("RELIANCE", MagicMock())
        self.assertTrue(result)

    def test_recover_ticker_angel_fails_fallback_to_yf(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        self.db_factory.return_value = mock_db

        self.historical_service.is_logged_in = True
        self.historical_service.get_historical_candles.side_effect = Exception("Angel One error")

        import pandas as pd
        df = pd.DataFrame({
            "Open": [100], "High": [105], "Low": [95],
            "Close": [102], "Volume": [1000],
        }, index=pd.DatetimeIndex([datetime(2026, 7, 1, 10, 0, 0)]))
        self.yf_downloader.download_single.return_value = (df,)
        self.yf_downloader.is_failed.return_value = False

        result = self.service.recover_ticker("RELIANCE", MagicMock())
        self.assertTrue(result)

    def test_recover_ticker_both_fail(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        self.db_factory.return_value = mock_db

        self.historical_service.is_logged_in = True
        self.historical_service.get_historical_candles.side_effect = Exception("Angel error")
        self.yf_downloader.download_single.side_effect = Exception("yfinance error")
        self.yf_downloader.is_failed.return_value = False

        result = self.service.recover_ticker("RELIANCE", MagicMock())
        # Recovery completes even with empty data (no data is not an error)
        self.assertTrue(result)

    def test_get_stats_empty(self):
        stats = self.service.get_stats()
        self.assertEqual(stats["recovered_tickers"], 0)
        self.assertEqual(stats["total_recoveries"], 0)
        self.assertEqual(stats["errors"], 0)

    def test_get_stats_after_recovery(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        self.db_factory.return_value = mock_db
        self.historical_service.is_logged_in = True
        self.historical_service.get_historical_candles.return_value = []
        self.service.recover_ticker("R1", MagicMock())
        self.service.recover_ticker("R2", MagicMock())
        stats = self.service.get_stats()
        self.assertEqual(stats["recovered_tickers"], 2)
        self.assertEqual(stats["total_recoveries"], 2)

    def test_recover_ticker_upsert_called(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        self.db_factory.return_value = mock_db
        self.historical_service.is_logged_in = True
        self.historical_service.get_historical_candles.return_value = [
            {"timestamp": datetime(2026, 7, 1, 10, 0, 0), "open": 100, "high": 105,
             "low": 95, "close": 102, "volume": 1000}
        ]
        mock_builder = MagicMock()
        self.service.recover_ticker("RELIANCE", mock_builder)
        self.historical_service.get_historical_candles.assert_called_once()


if __name__ == "__main__":
    unittest.main()
