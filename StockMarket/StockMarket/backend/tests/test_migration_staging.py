import unittest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from models import Candle
from migration.staging import staging_table_status, prepare_staging_table, StagingStatus


def _make_session_with_candles():
    engine = create_engine("sqlite:///:memory:")
    Candle.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


class TestStagingTableStatus(unittest.TestCase):
    def test_absent_when_table_does_not_exist(self):
        db = _make_session_with_candles()
        self.assertEqual(staging_table_status(db), StagingStatus.ABSENT)

    def test_compatible_empty_when_created_and_no_rows(self):
        db = _make_session_with_candles()
        db.execute(text("""
            CREATE TABLE candles_migration (
                id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
                timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
                close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN, created_at DATETIME
            )
        """))
        db.commit()
        self.assertEqual(staging_table_status(db), StagingStatus.COMPATIBLE_EMPTY)

    def test_compatible_1d_only_when_all_staged_rows_are_1D(self):
        db = _make_session_with_candles()
        db.execute(text("""
            CREATE TABLE candles_migration (
                id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
                timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
                close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN, created_at DATETIME
            )
        """))
        db.execute(text("""
            INSERT INTO candles_migration (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
            VALUES ('X', '1D', '2026-01-01', 1, 2, 0, 1, 100, 1, 'ANGELONE', 1)
        """))
        db.commit()
        self.assertEqual(staging_table_status(db), StagingStatus.COMPATIBLE_1D_ONLY)

    def test_incompatible_when_non_1D_rows_present(self):
        """Stale data from a different (non-1D-only) run must never be
        silently absorbed into a 1D-only run."""
        db = _make_session_with_candles()
        db.execute(text("""
            CREATE TABLE candles_migration (
                id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
                timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
                close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN, created_at DATETIME
            )
        """))
        db.execute(text("""
            INSERT INTO candles_migration (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
            VALUES ('X', '5m', '2026-01-01 09:15', 1, 2, 0, 1, 100, 1, 'ANGELONE', 1)
        """))
        db.commit()
        self.assertEqual(staging_table_status(db), StagingStatus.INCOMPATIBLE)

    def test_incompatible_when_schema_missing_columns(self):
        db = _make_session_with_candles()
        db.execute(text("CREATE TABLE candles_migration (ticker VARCHAR)"))
        db.commit()
        self.assertEqual(staging_table_status(db), StagingStatus.INCOMPATIBLE)


class TestPrepareStagingTable(unittest.TestCase):
    def test_creates_when_absent(self):
        db = _make_session_with_candles()
        result = prepare_staging_table(db)
        self.assertEqual(result, "created")
        self.assertEqual(staging_table_status(db), StagingStatus.COMPATIBLE_EMPTY)

    def test_reuses_when_compatible_empty(self):
        db = _make_session_with_candles()
        prepare_staging_table(db)  # create
        result = prepare_staging_table(db)  # second call
        self.assertEqual(result, "reused")

    def test_refuses_when_incompatible(self):
        db = _make_session_with_candles()
        db.execute(text("CREATE TABLE candles_migration (ticker VARCHAR)"))
        db.commit()
        with self.assertRaises(RuntimeError):
            prepare_staging_table(db)


if __name__ == "__main__":
    unittest.main()
