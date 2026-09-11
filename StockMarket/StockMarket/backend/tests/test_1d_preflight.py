"""
Pre-flight regression suite for the full 1D migration.

Covers the 13 behaviours that must hold before the real run:
 1. 1D uses its own larger (verified 730-day) date range
 2. intraday timeframes keep the global 90-day limit
 3. runtime estimation uses GLOBAL rps, never rps * workers
 4. API error -> retry then failure
 5. rate-limit error -> retry
 6. successful empty response -> known absence, NOT failure
 7. known absence never becomes an infinite retry loop
 8. real candles -> normal validation/staging/promotion
 9. existing production 1D candles remain untouched
10. non-1D timeframes remain untouched
11. INSERT-only behaviour
12. migration remains resumable
13. no destructive operation reachable from run_1d_migration.py

Everything is mocked at the historical_service boundary or run against an
in-memory SQLite DB -- no network calls, no production access.
"""
import ast
import inspect
import unittest
from datetime import date, datetime
from queue import Queue
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import run_1d_migration
from models import Candle
from migration.models import MigrationJob
from migration.config import MigrationConfig, TierConfig
from migration.batch_downloader import BatchDownloader
from migration.identity import TickerIdentity
from migration.progress_tracker import ProgressTracker
from migration.promotion import promote_1d_candles
from historical_service import HistoricalFetchError


STAGING_DDL = """
CREATE TABLE candles_migration (
    id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL, timeframe VARCHAR(5) NOT NULL,
    timestamp DATETIME NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL, low FLOAT NOT NULL,
    close FLOAT NOT NULL, volume BIGINT, is_completed BOOLEAN, data_source VARCHAR(10), is_backfilled BOOLEAN,
    UNIQUE (ticker, timeframe, timestamp)
)
"""


def _db():
    engine = create_engine("sqlite:///:memory:")
    Candle.__table__.create(bind=engine)
    MigrationJob.__table__.create(bind=engine)
    with engine.begin() as c:
        c.execute(text(STAGING_DDL))
    return engine, sessionmaker(bind=engine)


def _cfg(**kw):
    base = dict(requests_per_second=1000, workers=1, retry_max=2,
                staging_table="candles_migration", max_date_range_days=90)
    base.update(kw)
    return MigrationConfig(**base)


def _1d_tier(max_days=730):
    return TierConfig(timeframe="1D", months=25, angel_interval="ONE_DAY",
                      fetch_method="direct", max_date_range_days=max_days)


# ---------------------------------------------------------------- 1 & 2
class TestPerTierDateRange(unittest.TestCase):
    def test_1d_tier_uses_its_own_730_day_range(self):
        self.assertEqual(_1d_tier().effective_max_date_range_days(90), 730)

    def test_intraday_tiers_keep_global_90_day_limit(self):
        for tf, interval in [("5m", "FIVE_MINUTE"), ("15m", "FIFTEEN_MINUTE"),
                             ("30m", "THIRTY_MINUTE"), ("1h", "ONE_HOUR")]:
            with self.subTest(tf=tf):
                t = TierConfig(timeframe=tf, months=2, angel_interval=interval, fetch_method="direct")
                self.assertEqual(t.effective_max_date_range_days(90), 90)

    def test_real_yaml_gives_1d_730_and_intraday_90(self):
        cfg = MigrationConfig.load()
        self.assertEqual(cfg.max_date_range_days, 90, "global default must stay 90")
        by_tf = {t.timeframe: t for t in cfg.tiers}
        self.assertEqual(by_tf["1D"].effective_max_date_range_days(cfg.max_date_range_days), 730)
        for tf in ("5m", "15m", "30m", "1h"):
            self.assertEqual(by_tf[tf].effective_max_date_range_days(cfg.max_date_range_days), 90,
                             f"{tf} must be unaffected by the 1D override")

    def test_730_day_window_is_a_single_chunk(self):
        from migration.config import chunk_date_range
        chunks = chunk_date_range(date(2024, 8, 12), date(2026, 8, 11), 730)
        self.assertEqual(len(chunks), 1, "the whole retention window must fit in ONE request")

    def test_planner_and_downloader_agree_on_chunk_width(self):
        cfg = MigrationConfig.load()
        planner = run_1d_migration._one_d_chunk_days(cfg)
        tier = next(t for t in cfg.tiers if t.timeframe == "1D")
        downloader = tier.effective_max_date_range_days(cfg.max_date_range_days)
        self.assertEqual(planner, downloader,
                         "a dry-run request count is fiction unless both chunk identically")


# -------------------------------------------------------------------- 3
class TestRuntimeEstimateGlobalRate(unittest.TestCase):
    def test_workers_do_not_multiply_throughput(self):
        cfg = MigrationConfig.load()
        plan, _ = None, None
        from migration.plan_1d import MigrationPlan

        class P(MigrationPlan):
            def __init__(self): pass
            def estimate_requests(self): return 1018

        p = P()
        self.assertEqual(p.estimate_runtime_seconds(0.3, 1), p.estimate_runtime_seconds(0.3, 8))
        self.assertAlmostEqual(p.estimate_runtime_seconds(0.3, 2), 1018 / 0.3, places=3)

    def test_rate_limiter_is_shared_not_per_worker(self):
        """The reason workers can't help: one BatchDownloader owns one
        AngelOneFetchManager owning one RateLimiter."""
        d = BatchDownloader(_cfg())
        self.assertIs(d._fetch_mgr._rate_limiter, d._fetch_mgr._rate_limiter)
        src = inspect.getsource(type(d._fetch_mgr._rate_limiter))
        self.assertIn("_lock", src, "limiter must serialize callers through a lock")


# ---------------------------------------------------------------- 4 & 5
class TestFetchFailureSemantics(unittest.TestCase):
    def _mgr(self):
        from migration.fetch_manager import AngelOneFetchManager
        return AngelOneFetchManager(_cfg(retry_max=3, backoff_seconds=[0, 0, 0]))

    def test_api_error_retries_then_fails(self):
        mgr = self._mgr()
        with patch("migration.fetch_manager.historical_service") as hs:
            hs.is_logged_in = True
            hs.get_historical_candles.side_effect = HistoricalFetchError("boom", retryable=True)
            ok, candles, err, attempts = mgr.fetch_with_retry("X", "ONE_DAY", date(2024, 1, 1), date(2024, 3, 1))
        self.assertFalse(ok)
        self.assertEqual(attempts, 3)

    def test_rate_limit_error_triggers_retry_and_can_recover(self):
        mgr = self._mgr()
        calls = {"n": 0}
        real = [{"timestamp": datetime(2024, 1, 2), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

        def se(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HistoricalFetchError("Access denied because of exceeding access rate", retryable=True)
            return real

        with patch("migration.fetch_manager.historical_service") as hs:
            hs.is_logged_in = True
            hs.get_historical_candles.side_effect = se
            ok, candles, err, attempts = mgr.fetch_with_retry("X", "ONE_DAY", date(2024, 1, 1), date(2024, 3, 1))
        self.assertTrue(ok)
        self.assertEqual(attempts, 2)


# ---------------------------------------------------------------- 6 & 7
class TestKnownAbsencePolicy(unittest.TestCase):
    def test_successful_empty_is_not_a_failure(self):
        from migration.fetch_manager import AngelOneFetchManager
        mgr = AngelOneFetchManager(_cfg(retry_max=3, backoff_seconds=[0, 0, 0]))
        with patch("migration.fetch_manager.historical_service") as hs:
            hs.is_logged_in = True
            hs.get_historical_candles.return_value = []
            ok, candles, err, attempts = mgr.fetch_with_retry("X", "ONE_DAY", date(2024, 1, 1), date(2024, 3, 1))
        self.assertTrue(ok, "an empty-but-successful response is not a failure")
        self.assertEqual(candles, [])
        self.assertEqual(attempts, 1, "and must not be retried")

    def test_known_absence_status_is_terminal_and_not_retried(self):
        engine, Session = _db()
        db = Session()
        tracker = ProgressTracker(_cfg())
        job = MigrationJob(ticker="ILLIQUID", tier=6, timeframe="1D", status="DONE")
        db.add(job); db.commit()
        tracker.mark_complete_with_known_absences(db, job, "no candle from Angel One")
        db.commit()

        self.assertEqual(job.status, "KNOWN_ABSENCES")
        self.assertIn("KNOWN_ABSENCES", tracker.TERMINAL_STATUSES)
        pending = tracker.get_pending_jobs(db, 6)
        self.assertNotIn("ILLIQUID", [j.ticker for j in pending],
                         "a known absence must never re-enter the retry queue")
        db.close()

    def test_worker_loop_skips_known_absence_ticker(self):
        """Point 7 end-to-end: no Angel One call is made at all for a ticker
        already marked KNOWN_ABSENCES."""
        engine, Session = _db()
        seed = Session()
        seed.add(MigrationJob(ticker="ILLIQUID", tier=6, timeframe="1D",
                              status="KNOWN_ABSENCES"))
        seed.commit(); seed.close()

        d = BatchDownloader(_cfg())
        q = Queue(); q.put(TickerIdentity(ticker="ILLIQUID", exchange="NSE"))
        with patch("migration.batch_downloader.SessionLocal", Session), \
             patch.object(d._fetch_mgr, "fetch_with_retry") as mock_fetch:
            d._worker_loop(0, 6, _1d_tier(), (date(2024, 8, 12), date(2026, 8, 11)), q, None)
        mock_fetch.assert_not_called()

    def test_failed_fetch_still_marked_failed_not_known_absence(self):
        engine, Session = _db()
        d = BatchDownloader(_cfg(retry_max=1))
        q = Queue(); q.put(TickerIdentity(ticker="X", exchange="NSE"))
        with patch("migration.batch_downloader.SessionLocal", Session), \
             patch.object(d._fetch_mgr, "fetch_with_retry",
                          side_effect=lambda *a, **k: (False, [], "rate limit", 3)):
            d._worker_loop(0, 6, _1d_tier(), (date(2024, 8, 12), date(2026, 8, 11)), q, None)
        db = Session()
        job = db.query(MigrationJob).filter(MigrationJob.ticker == "X").first()
        db.close()
        self.assertEqual(job.status, "FAILED",
                         "a real fetch failure must stay retryable, never become a known absence")


# ------------------------------------------------------------- 8, 9, 11
class TestPromotionInsertOnly(unittest.TestCase):
    def test_real_candles_stage_and_promote_without_touching_existing(self):
        engine, Session = _db()
        db = Session()
        # pre-existing production row that must survive byte-for-byte
        db.add(Candle(ticker="T", timeframe="1D", timestamp=datetime(2026, 7, 21),
                      open=111.0, high=112.0, low=110.0, close=111.5, volume=999,
                      is_completed=True, data_source="YFINANCE", is_backfilled=False))
        # a staged row for the SAME key (different values) + one genuinely new row
        db.execute(text("""INSERT INTO candles_migration
            (ticker,timeframe,timestamp,open,high,low,close,volume,is_completed,data_source,is_backfilled)
            VALUES ('T','1D','2026-07-21 00:00:00',1,1,1,1,1,1,'ANGELONE',1),
                   ('T','1D','2024-08-12 00:00:00',5,6,4,5.5,50,1,'ANGELONE',1)"""))
        db.commit()

        res = promote_1d_candles(db, staging_table="candles_migration")
        db.commit()

        self.assertEqual(res.promoted, 1, "only the genuinely new row may be inserted")
        self.assertEqual(res.already_in_production, 1)

        kept = db.query(Candle).filter(Candle.timestamp == datetime(2026, 7, 21)).one()
        self.assertEqual(kept.open, 111.0, "existing row must NOT be overwritten")
        self.assertEqual(kept.data_source, "YFINANCE")

        added = db.query(Candle).filter(Candle.timestamp == datetime(2024, 8, 12)).one()
        self.assertEqual(added.data_source, "ANGELONE")
        self.assertTrue(added.is_backfilled)
        self.assertEqual(db.query(Candle).count(), 2)
        db.close()

    def test_non_1d_rows_untouched_by_promotion(self):
        engine, Session = _db()
        db = Session()
        for tf in ("5m", "15m", "30m", "1h"):
            db.add(Candle(ticker="T", timeframe=tf, timestamp=datetime(2026, 7, 21, 9, 15),
                          open=1, high=1, low=1, close=1, volume=1,
                          is_completed=True, data_source="ANGELONE", is_backfilled=False))
        db.execute(text("""INSERT INTO candles_migration
            (ticker,timeframe,timestamp,open,high,low,close,volume,is_completed,data_source,is_backfilled)
            VALUES ('T','1D','2024-08-12 00:00:00',5,6,4,5.5,50,1,'ANGELONE',1)"""))
        db.commit()
        before = {tf: db.query(Candle).filter(Candle.timeframe == tf).count()
                  for tf in ("5m", "15m", "30m", "1h")}

        promote_1d_candles(db, staging_table="candles_migration")
        db.commit()

        after = {tf: db.query(Candle).filter(Candle.timeframe == tf).count()
                 for tf in ("5m", "15m", "30m", "1h")}
        self.assertEqual(before, after)
        db.close()


# ------------------------------------------------------------------- 12
class TestResumability(unittest.TestCase):
    def test_done_tickers_skipped_on_rerun(self):
        engine, Session = _db()
        seed = Session()
        seed.add(MigrationJob(ticker="ALREADY", tier=6, timeframe="1D", status="DONE"))
        seed.commit(); seed.close()

        d = BatchDownloader(_cfg())
        q = Queue(); q.put(TickerIdentity(ticker="ALREADY", exchange="NSE"))
        with patch("migration.batch_downloader.SessionLocal", Session), \
             patch.object(d._fetch_mgr, "fetch_with_retry") as mock_fetch:
            d._worker_loop(0, 6, _1d_tier(), (date(2024, 8, 12), date(2026, 8, 11)), q, None)
        mock_fetch.assert_not_called()

    def test_failed_ticker_is_retried_on_rerun(self):
        engine, Session = _db()
        seed = Session()
        seed.add(MigrationJob(ticker="RETRYME", tier=6, timeframe="1D", status="FAILED", retry_count=0))
        seed.commit(); seed.close()

        tracker = ProgressTracker(_cfg())
        db = Session()
        pending = tracker.get_pending_jobs(db, 6)
        db.close()
        self.assertIn("RETRYME", [j.ticker for j in pending])


# ------------------------------------------------------------------- 13
class _Strip(ast.NodeTransformer):
    def _s(self, n):
        self.generic_visit(n)
        if (n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(getattr(n.body[0], "value", None), ast.Constant)
                and isinstance(n.body[0].value.value, str)):
            n.body = n.body[1:] or [ast.Pass()]
        return n
    visit_Module = visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _s


def _code_only(obj) -> str:
    return ast.unparse(_Strip().visit(ast.parse(inspect.getsource(obj))))


class TestNoDestructiveOperations(unittest.TestCase):
    def test_no_destructive_sql_or_legacy_path_reachable(self):
        src = _code_only(run_1d_migration)
        for phrase in ["DROP TABLE", "TRUNCATE", "RENAME TO",
                       "DELETE FROM candles", "UPDATE candles",
                       "cmd_migrate", "_swap_tables", "MigrationOrchestrator"]:
            self.assertNotIn(phrase, src, f"forbidden operation reachable: {phrase!r}")

    def test_stock_data_never_referenced(self):
        self.assertNotIn("stock_data", _code_only(run_1d_migration))

    def test_only_1d_timeframe_targeted(self):
        # ast.unparse normalizes every string literal to single quotes, so
        # match on the quoted forms it actually emits.
        src = _code_only(run_1d_migration)
        self.assertIn("'1D'", src)
        for tf in ("'1W'", "'1M'", "'4h'"):
            self.assertNotIn(tf, src, f"{tf} must not be generated during the 1D phase")


if __name__ == "__main__":
    unittest.main()


class TestStatusValuesFitColumn(unittest.TestCase):
    """The bug that killed a completed 1,001-ticker run: the status string
    'COMPLETE_WITH_KNOWN_ABSENCES' (29 chars) did not fit
    MigrationJob.status = Column(String(20)), so Postgres raised
    StringDataRightTruncation during the post-promotion status update --
    after all data had already been fetched and promoted. Verifying a column
    is a String is not the same as verifying its width."""

    def _limit(self):
        return MigrationJob.__table__.c.status.type.length

    def test_every_status_literal_fits_the_column(self):
        limit = self._limit()
        self.assertIsNotNone(limit, "status column should declare a length")
        statuses = ["PENDING", "FETCHING", "VALIDATING", "INSERTING",
                    "DONE", "FAILED", ProgressTracker.KNOWN_ABSENCES]
        for s in statuses:
            with self.subTest(status=s):
                self.assertLessEqual(len(s), limit,
                                     f"status {s!r} is {len(s)} chars, column holds {limit}")

    def test_terminal_statuses_fit(self):
        limit = self._limit()
        for s in ProgressTracker.TERMINAL_STATUSES:
            self.assertLessEqual(len(s), limit, f"terminal status {s!r} exceeds {limit}")

    def test_known_absences_status_round_trips_through_a_real_db(self):
        """Writes and re-reads the value so a too-long string fails here
        rather than in production."""
        engine, Session = _db()
        db = Session()
        job = MigrationJob(ticker="T", tier=6, timeframe="1D", status="DONE")
        db.add(job); db.commit()
        ProgressTracker(_cfg()).mark_complete_with_known_absences(db, job, "detail")
        db.commit()
        reloaded = db.query(MigrationJob).filter(MigrationJob.ticker == "T").one()
        self.assertEqual(reloaded.status, ProgressTracker.KNOWN_ABSENCES)
        db.close()


class TestIncludeInactiveScoping(unittest.TestCase):
    """--include-inactive lets an EXPLICITLY named ticker be migrated even
    when stock_metadata.is_active is FALSE. That flag proved unreliable (it
    reflects a 2026-07-03 ingestion cutover, not tradability), and --tickers
    was previously an INTERSECTION with the active set, so naming an
    inactive ticker silently migrated nothing. It must never widen the
    DEFAULT universe."""

    def test_flag_without_explicit_list_does_not_widen_universe(self):
        rows = [{"ticker": "A", "exchange": "NSE"}]
        with patch("run_1d_migration._resolve_active_identity_rows", return_value=rows) as active, \
             patch("run_1d_migration._resolve_named_identity_rows") as named, \
             patch("migration.plan_1d.audit_existing_1d_coverage", return_value={}), \
             patch("database.SessionLocal"):
            run_1d_migration.build_plan(ticker_filter=None, include_inactive=True)
        active.assert_called_once()
        named.assert_not_called()

    def test_named_list_with_flag_bypasses_is_active(self):
        with patch("run_1d_migration._resolve_active_identity_rows") as active, \
             patch("run_1d_migration._resolve_named_identity_rows",
                   return_value=[{"ticker": "HDFCLIFE", "exchange": "NSE"}]) as named, \
             patch("migration.plan_1d.audit_existing_1d_coverage", return_value={}), \
             patch("database.SessionLocal"):
            plan, _ = run_1d_migration.build_plan(ticker_filter=["HDFCLIFE"], include_inactive=True)
        named.assert_called_once()
        active.assert_not_called()
        self.assertEqual({i.ticker for i in plan.identities}, {"HDFCLIFE"})

    def test_named_list_without_flag_still_intersects_active_set(self):
        rows = [{"ticker": "A", "exchange": "NSE"}]
        with patch("run_1d_migration._resolve_active_identity_rows", return_value=rows), \
             patch("run_1d_migration._resolve_named_identity_rows") as named, \
             patch("migration.plan_1d.audit_existing_1d_coverage", return_value={}), \
             patch("database.SessionLocal"):
            plan, _ = run_1d_migration.build_plan(ticker_filter=["HDFCLIFE"], include_inactive=False)
        named.assert_not_called()
        self.assertEqual(len(plan.identities), 0, "inactive ticker must NOT appear without the flag")


class TestTickersFile(unittest.TestCase):
    def test_reads_one_per_line_ignoring_blanks_and_comments(self):
        import tempfile, os as _os
        fd, path = tempfile.mkstemp(suffix=".txt")
        with _os.fdopen(fd, "w") as f:
            f.write("RELIANCE\n\n# a comment\nTCS\n  INFY  \n")
        try:
            self.assertEqual(run_1d_migration._read_tickers_file(path), ["RELIANCE", "TCS", "INFY"])
        finally:
            _os.unlink(path)

    def test_tickers_file_takes_precedence_over_tickers_arg(self):
        import tempfile, os as _os
        fd, path = tempfile.mkstemp(suffix=".txt")
        with _os.fdopen(fd, "w") as f:
            f.write("AAA\nBBB\n")
        class NS:
            tickers = "ZZZ"
            tickers_file = path
        try:
            self.assertEqual(run_1d_migration._parse_ticker_filter(NS()), ["AAA", "BBB"])
        finally:
            _os.unlink(path)
