"""
Tests for migration/repair_1d.py -- fetches explicit missing date ranges
(as opposed to batch_downloader.py's existing-coverage narrowing) and
stages them additively. Mocked AngelOneFetchManager.fetch_with_retry, no
real Angel One calls; a real in-memory SQLite staging table.
"""
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from migration.config import MigrationConfig
from migration.fetch_manager import AngelOneFetchManager
from migration.validator import MigrationValidator
from migration.repair_1d import repair_ticker_gaps


def _make_staging_session():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE candles_migration (
                id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
                timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
                close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN,
                UNIQUE (ticker, timeframe, timestamp)
            )
        """))
    return sessionmaker(bind=engine)()


def _cfg():
    return MigrationConfig(requests_per_second=1000, retry_max=1, staging_table="candles_migration", max_date_range_days=90)


class TestRepairFetchesOnlyMissingRanges(unittest.TestCase):
    def test_fetches_and_stages_exactly_the_requested_gap(self):
        db = _make_staging_session()
        cfg = _cfg()
        fetch_mgr = MagicMock(spec=AngelOneFetchManager)
        fake_candles = [
            {"timestamp": datetime(2024, 8, 12), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
            {"timestamp": datetime(2024, 8, 13), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
        ]
        fetch_mgr.fetch_with_retry.return_value = (True, fake_candles, None, 1)

        result = repair_ticker_gaps(
            db, cfg, fetch_mgr, MigrationValidator(),
            ticker="RELIANCE", exchange="NSE",
            missing_ranges=[(date(2024, 8, 12), date(2024, 8, 13))],
        )

        self.assertEqual(result.candles_fetched, 2)
        self.assertEqual(result.candles_staged, 2)
        self.assertTrue(result.validation_passed)
        self.assertEqual(result.chunks_failed, 0)

        staged = db.execute(text("SELECT COUNT(*) FROM candles_migration WHERE ticker='RELIANCE'")).scalar()
        self.assertEqual(staged, 2)

    def test_empty_missing_ranges_is_a_noop(self):
        db = _make_staging_session()
        fetch_mgr = MagicMock(spec=AngelOneFetchManager)
        result = repair_ticker_gaps(
            db, _cfg(), fetch_mgr, MigrationValidator(),
            ticker="RELIANCE", exchange="NSE", missing_ranges=[],
        )
        fetch_mgr.fetch_with_retry.assert_not_called()
        self.assertEqual(result.candles_fetched, 0)

    def test_re_running_same_range_is_idempotent(self):
        """ON CONFLICT DO NOTHING must make a second repair call for the
        same range a safe no-op on the already-staged rows."""
        db = _make_staging_session()
        cfg = _cfg()
        fetch_mgr = MagicMock(spec=AngelOneFetchManager)
        fake_candles = [
            {"timestamp": datetime(2024, 8, 12), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
        ]
        fetch_mgr.fetch_with_retry.return_value = (True, fake_candles, None, 1)

        repair_ticker_gaps(db, cfg, fetch_mgr, MigrationValidator(), "RELIANCE", "NSE",
                            [(date(2024, 8, 12), date(2024, 8, 12))])
        result2 = repair_ticker_gaps(db, cfg, fetch_mgr, MigrationValidator(), "RELIANCE", "NSE",
                                      [(date(2024, 8, 12), date(2024, 8, 12))])

        self.assertEqual(result2.candles_staged, 0, "second run must insert nothing new (ON CONFLICT DO NOTHING)")
        total = db.execute(text("SELECT COUNT(*) FROM candles_migration WHERE ticker='RELIANCE'")).scalar()
        self.assertEqual(total, 1)


class TestRepairChunkFailure(unittest.TestCase):
    def test_failed_chunk_recorded_not_silently_dropped(self):
        db = _make_staging_session()
        cfg = _cfg()
        fetch_mgr = MagicMock(spec=AngelOneFetchManager)
        fetch_mgr.fetch_with_retry.return_value = (False, [], "Access denied because of exceeding access rate", 3)

        result = repair_ticker_gaps(
            db, cfg, fetch_mgr, MigrationValidator(), "RELIANCE", "NSE",
            missing_ranges=[(date(2024, 8, 12), date(2024, 8, 13))],
        )
        self.assertEqual(result.chunks_failed, 1)
        self.assertEqual(result.candles_fetched, 0)
        self.assertTrue(any("exceeding access rate" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
