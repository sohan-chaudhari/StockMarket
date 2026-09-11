"""
Regression test for a bug found by a real controlled 1D execution (not by
simulation): BatchDownloader._worker_loop computed a correctly per-ticker-
narrowed `ticker_date_range` for the 1D tier and used it to decide WHAT to
fetch, but then validated the fetched candles against the original,
tier-global `date_range` instead -- so any 1D ticker with existing recent
coverage (the normal case) had its correctly-fetched, correctly-dated older
candles rejected by the validator's date_range check, because the stale
global range didn't cover them. Confirmed live: RELIANCE/TCS/INFY each
failed "5/6 checks passed" (date_range) on real Angel One data before this
fix, with zero rows reaching staging.
"""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

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


class TestValidationUsesNarrowedRange(unittest.TestCase):
    def test_fetched_candles_outside_stale_tier_range_but_inside_narrowed_range_are_not_rejected(self):
        engine, Session = _make_engine_and_sessionmaker()

        # Ticker already has SOME recent 1D coverage in `candles` -- this is
        # exactly what makes compute_1d_required_range narrow the fetch
        # range to end BEFORE that existing coverage, which is what exposed
        # the bug (the real chunk requested is older than, and structurally
        # different from, the stale tier-global range). existing_start is
        # deliberately "yesterday" (not a fixed calendar date) so this test
        # never goes stale as real time passes -- batch_downloader.py
        # computes required_start from date.today() - retention_days, and a
        # fixed existing_start would eventually drift behind that moving
        # required_start and start rejecting fake_candles for real (see the
        # bug this exact staleness caused when the fixed date was 2026-07-21
        # and required_start's floor moved past the fake candles' start).
        from migration.config import get_1d_retention_days
        existing_start_dt = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
        seed_db = Session()
        seed_db.add(Candle(
            ticker="RELIANCE", timeframe="1D", timestamp=existing_start_dt,
            open=1, high=1, low=1, close=1, volume=1,
            is_completed=True, data_source="YFINANCE", is_backfilled=False,
        ))
        seed_db.commit()
        seed_db.close()

        # Angel One returns real candles dated at the true narrowed
        # required_start (date.today() - retention_days) -- well before the
        # existing coverage above, and deliberately OUTSIDE the stale
        # tier-global range this test sets up below, reproducing the bug.
        required_start = date.today() - timedelta(days=get_1d_retention_days())
        fake_candles = [
            {"timestamp": datetime.combine(required_start, datetime.min.time()) + timedelta(days=i),
             "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
            for i in range(5)
        ]

        cfg = MigrationConfig(requests_per_second=1000, workers=1, retry_max=1,
                               staging_table="candles_migration", max_date_range_days=90)
        tier_cfg = TierConfig(timeframe="1D", months=None, angel_interval="ONE_DAY", fetch_method="direct")

        # A stale/unrelated tier-global range (simulates migration.yaml's
        # coarse estimate) that does NOT cover where the real fetch lands --
        # exactly the scenario that broke before the fix.
        stale_tier_range = (date(2025, 1, 1), date(2025, 6, 1))

        downloader = BatchDownloader(cfg)

        # Create the staging table on the same in-memory engine.
        from models import Base as StockBase
        StockBase.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("""
                CREATE TABLE candles_migration (
                    id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
                    timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
                    close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN,
                    UNIQUE (ticker, timeframe, timestamp)
                )
            """))

        # The narrowed range spans multiple 90-day chunks (8, for a ~709-day
        # gap) -- fetch_with_retry is called once per chunk. Only the first
        # chunk call returns real candles; the rest return empty (as Angel
        # One legitimately would for chunks with no data, e.g. before a
        # ticker's listing date). A static return_value here would return
        # the same 5 candles for every chunk, producing 35 artificial
        # duplicates unrelated to the bug under test.
        call_count = {"n": 0}

        def fetch_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (True, fake_candles, None, 1)
            return (True, [], None, 1)

        with patch("migration.batch_downloader.SessionLocal", Session), \
             patch.object(downloader._fetch_mgr, "fetch_with_retry", side_effect=fetch_side_effect):
            downloader._worker_loop(
                worker_id=0, tier_index=6, tier_cfg=tier_cfg,
                date_range=stale_tier_range,
                queue=_one_item_queue(TickerIdentity(ticker="RELIANCE", exchange="NSE")),
                progress_callback=None,
            )

        verify_db = Session()
        staged = verify_db.execute(
            __import__("sqlalchemy").text("SELECT COUNT(*) FROM candles_migration WHERE ticker='RELIANCE'")
        ).scalar()
        job = verify_db.query(MigrationJob).filter(MigrationJob.ticker == "RELIANCE").first()
        verify_db.close()

        self.assertEqual(staged, 5, "all 5 fetched candles must reach staging -- validated against the "
                                     "correctly narrowed per-ticker range, not the stale tier-global one")
        self.assertEqual(job.status, "DONE", f"job should be DONE, not FAILED (error: {job.error_message!r})")


def _one_item_queue(item):
    from queue import Queue
    q = Queue()
    q.put(item)
    return q


if __name__ == "__main__":
    unittest.main()
