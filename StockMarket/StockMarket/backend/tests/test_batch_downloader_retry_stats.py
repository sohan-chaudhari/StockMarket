"""
Point 7 (job-level) + point 8 from the retry/failure fix requirements:
a chunk that fails after exhausting retries must mark the ticker's
MigrationJob FAILED, never DONE -- and BatchDownloader's own stats dict
must truthfully report the failure and the retries that occurred, instead
of the previous 'failed_api_calls: 0, total_retries: 0' even when real API
attempts failed (the exact discrepancy observed in the second controlled
batch's raw output).
"""
import unittest
from datetime import date, datetime
from queue import Queue
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Candle
from migration.models import MigrationJob
from migration.config import MigrationConfig, TierConfig
from migration.batch_downloader import BatchDownloader
from migration.identity import TickerIdentity


def _make_engine_and_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Candle.__table__.create(bind=engine)
    MigrationJob.__table__.create(bind=engine)
    return engine, sessionmaker(bind=engine)


class TestFailedChunkNeverProducesDone(unittest.TestCase):
    def test_exhausted_retries_marks_job_failed_not_done(self):
        engine, Session = _make_engine_and_sessionmaker()
        cfg = MigrationConfig(requests_per_second=1000, workers=1, retry_max=1,
                               staging_table="candles_migration", max_date_range_days=90)
        tier_cfg = TierConfig(timeframe="1D", months=None, angel_interval="ONE_DAY", fetch_method="direct")

        with create_engine("sqlite:///:memory:").begin():
            pass  # placeholder engine unused; staging created below on real engine

        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE candles_migration (
                    id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
                    timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
                    close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN,
                    UNIQUE (ticker, timeframe, timestamp)
                )
            """))

        downloader = BatchDownloader(cfg)

        # fetch_with_retry itself already exhausted retries and reports
        # failure -- this simulates the honest post-fix contract.
        def fetch_side_effect(*args, **kwargs):
            return (False, [], "Access denied because of exceeding access rate", 3)

        with patch("migration.batch_downloader.SessionLocal", Session), \
             patch.object(downloader._fetch_mgr, "fetch_with_retry", side_effect=fetch_side_effect):
            q = Queue()
            q.put(TickerIdentity(ticker="RELIANCE", exchange="NSE"))
            downloader._worker_loop(
                worker_id=0, tier_index=6, tier_cfg=tier_cfg,
                date_range=(date(2024, 1, 1), date(2024, 3, 1)),
                queue=q, progress_callback=None,
            )

        verify_db = Session()
        job = verify_db.query(MigrationJob).filter(MigrationJob.ticker == "RELIANCE").first()
        staged = verify_db.execute(
            __import__("sqlalchemy").text("SELECT COUNT(*) FROM candles_migration WHERE ticker='RELIANCE'")
        ).scalar()
        verify_db.close()

        self.assertEqual(job.status, "FAILED", "a chunk that never succeeded must never leave the job DONE")
        self.assertEqual(staged, 0, "no candles should have reached staging for a failed chunk")

        stats = downloader.get_stats()
        self.assertGreater(stats["failed_api_calls"], 0, "a real failed chunk must be reflected in failed_api_calls")

    def test_retries_reported_truthfully_in_stats(self):
        """The exact discrepancy from the live run: attempts > 1 (real
        retries happened) must increment total_retries, not leave it at 0
        while candles still came back successfully on a later attempt."""
        engine, Session = _make_engine_and_sessionmaker()
        cfg = MigrationConfig(requests_per_second=1000, workers=1, retry_max=3,
                               staging_table="candles_migration", max_date_range_days=90)
        tier_cfg = TierConfig(timeframe="1D", months=None, angel_interval="ONE_DAY", fetch_method="direct")

        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE candles_migration (
                    id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
                    timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
                    close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN,
                    UNIQUE (ticker, timeframe, timestamp)
                )
            """))

        seed_db = Session()
        seed_db.add(Candle(
            ticker="RELIANCE", timeframe="1D", timestamp=datetime(2026, 7, 21),
            open=1, high=1, low=1, close=1, volume=1,
            is_completed=True, data_source="YFINANCE", is_backfilled=False,
        ))
        seed_db.commit()
        seed_db.close()

        downloader = BatchDownloader(cfg)
        fake_candles = [
            {"timestamp": datetime(2024, 8, 11), "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
        ]
        call_count = {"n": 0}

        def fetch_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Succeeded, but only after 2 internal retries.
                return (True, fake_candles, None, 3)
            return (True, [], None, 1)

        with patch("migration.batch_downloader.SessionLocal", Session), \
             patch.object(downloader._fetch_mgr, "fetch_with_retry", side_effect=fetch_side_effect):
            q = Queue()
            q.put(TickerIdentity(ticker="RELIANCE", exchange="NSE"))
            downloader._worker_loop(
                worker_id=0, tier_index=6, tier_cfg=tier_cfg,
                date_range=(date(2025, 1, 1), date(2025, 6, 1)),
                queue=q, progress_callback=None,
            )

        stats = downloader.get_stats()
        self.assertEqual(stats["total_retries"], 2, "2 retries occurred on the first chunk and must be counted")
        self.assertEqual(stats["failed_api_calls"], 0, "the chunk ultimately succeeded, so no failed call")


if __name__ == "__main__":
    unittest.main()
