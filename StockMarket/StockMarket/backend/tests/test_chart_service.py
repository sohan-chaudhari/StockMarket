import unittest
from datetime import datetime
from unittest.mock import MagicMock
from chart_service import ChartService


class FakeCandle:
    def __init__(self, ticker, tf, ts, o, h, l, c, v):
        self.ticker = ticker
        self.timeframe = tf
        self.timestamp = ts
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v
        self.is_completed = True


class TestChartService(unittest.TestCase):
    def setUp(self):
        self.live_mgr = MagicMock()
        self.live_mgr.get_current.return_value = None
        self.cache = MagicMock()
        self.cache.get.return_value = None
        self.resample_svc = MagicMock()
        self.db_factory = MagicMock()
        self.service = ChartService(
            live_mgr=self.live_mgr,
            cache=self.cache,
            resample_svc=self.resample_svc,
            db_session_factory=self.db_factory,
        )

    def test_get_chart_unknown_tf(self):
        result = self.service.get_chart("RELIANCE", "3m")
        self.assertEqual(result["metadata"]["error"], "Unknown timeframe: 3m")
        self.assertEqual(result["candles"], [])

    def test_get_chart_5m_from_db(self):
        ts = datetime(2026, 7, 1, 10, 0, 0)
        mock_db = MagicMock()
        mock_q = MagicMock()
        mock_q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            FakeCandle("RELIANCE", "5m", ts, 100, 105, 95, 102, 1000)
        ]
        mock_db.query.return_value = mock_q
        self.db_factory.return_value = mock_db

        result = self.service.get_chart("RELIANCE", "5m")
        self.assertEqual(len(result["candles"]), 1)
        self.assertEqual(result["metadata"]["source"], "db")

    def test_get_chart_5m_from_cache(self):
        ts_epoch = int(datetime(2026, 7, 1, 10, 0, 0).timestamp())
        self.cache.get.return_value = [{"time": ts_epoch, "open": 100, "high": 105,
                                         "low": 95, "close": 102, "volume": 1000}]
        self.live_mgr.get_current.return_value = None
        result = self.service.get_chart("RELIANCE", "5m")
        self.assertEqual(result["metadata"]["source"], "cache")
        self.assertEqual(len(result["candles"]), 1)

    def test_get_chart_live_snapshot_merged(self):
        ts = datetime(2026, 7, 1, 10, 0, 0)
        self.cache.get.return_value = None
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            FakeCandle("RELIANCE", "15m", ts, 100, 105, 95, 102, 1000)
        ]
        mock_db.query.return_value = mock_query
        self.db_factory.return_value = mock_db

        self.live_mgr.get_current.return_value = {
            "completed": [
                {"time": int(ts.timestamp()), "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
            ],
            "forming": {"time": int(ts.timestamp()) + 900, "open": 102, "high": 108, "low": 100, "close": 105, "volume": 500},
            "viewer_count": 1,
        }
        result = self.service.get_chart("RELIANCE", "15m")
        self.assertTrue(result["metadata"]["live"])
        self.assertTrue(result["metadata"]["forming_candle"])

    def test_get_chart_dedup(self):
        ts_epoch = 1788300000
        self.cache.get.return_value = None
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value = mock_query
        self.db_factory.return_value = mock_db

        self.resample_svc.resample_5m_to.return_value = [
            {"timestamp": datetime.fromtimestamp(ts_epoch), "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
        ]

        self.live_mgr.get_current.return_value = {
            "completed": [],
            "forming": {"time": ts_epoch, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
            "viewer_count": 1,
        }
        result = self.service.get_chart("RELIANCE", "15m")
        # Should not have duplicate entries for the same time
        times = [c["time"] for c in result["candles"]]
        self.assertEqual(len(times), len(set(times)))

    def test_get_chart_metadata_live(self):
        ts = datetime(2026, 7, 1, 10, 0, 0)
        self.cache.get.return_value = None
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value = mock_query
        self.db_factory.return_value = mock_db
        self.resample_svc.resample_5m_to.return_value = None

        self.live_mgr.get_current.return_value = {
            "completed": [], "forming": {"time": int(ts.timestamp()), "open": 100,
             "high": 105, "low": 95, "close": 102, "volume": 1000}, "viewer_count": 1,
        }
        result = self.service.get_chart("RELIANCE", "1h")
        self.assertIn("live", result["metadata"])
        self.assertIn("forming_candle", result["metadata"])
        self.assertIn("source", result["metadata"])
        self.assertIn("count", result["metadata"])

    def test_get_chart_count_metadata(self):
        ts = datetime(2026, 7, 1, 10, 0, 0)
        self.cache.get.return_value = None
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value = mock_query
        self.db_factory.return_value = mock_db
        self.live_mgr.get_current.return_value = None

        result = self.service.get_chart("RELIANCE", "15m")
        self.assertEqual(result["metadata"]["count"], len(result["candles"]))


if __name__ == "__main__":
    unittest.main()
