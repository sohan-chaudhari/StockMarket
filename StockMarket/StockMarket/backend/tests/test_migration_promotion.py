"""
Tests for migration/promotion.py -- the staging -> production 1D promotion
path. Uses a real in-memory SQLite database (not mocks) bound to the ACTUAL
`Candle` ORM model, so the exact same code path promote_1d_candles() uses in
production is exercised here -- only the engine differs. Zero interaction
with the real Postgres database; nothing here touches production data.
"""
import unittest
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from models import Candle, Base
from migration.promotion import (
    promote_1d_candles, reconcile_1d_promotion, validate_candle_row, PromotionResult,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Candle.__table__.create(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE candles_migration (
                id INTEGER PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                timeframe VARCHAR(5) NOT NULL,
                timestamp DATETIME NOT NULL,
                open FLOAT NOT NULL,
                high FLOAT NOT NULL,
                low FLOAT NOT NULL,
                close FLOAT NOT NULL,
                volume BIGINT,
                is_completed BOOLEAN,
                data_source VARCHAR(10),
                is_backfilled BOOLEAN
            )
        """))
    Session = sessionmaker(bind=engine)
    return Session()


def _stage(db, ticker, ts, o, h, l, c, v, timeframe="1D"):
    db.execute(text("""
        INSERT INTO candles_migration (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
        VALUES (:t, :tf, :ts, :o, :h, :l, :c, :v, 1, 'ANGELONE', 1)
    """), {"t": ticker, "tf": timeframe, "ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v})
    db.commit()


def _production_count(db, ticker=None, timeframe="1D"):
    q = db.query(Candle).filter(Candle.timeframe == timeframe)
    if ticker:
        q = q.filter(Candle.ticker == ticker)
    return q.count()


class TestValidateCandleRow(unittest.TestCase):
    def test_valid_row_passes(self):
        row = {"ticker": "X", "timestamp": datetime(2026, 1, 1), "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        self.assertIsNone(validate_candle_row(row))

    def test_invalid_ohlc_rejected(self):
        row = {"ticker": "X", "timestamp": datetime(2026, 1, 1), "open": 100, "high": 90, "low": 110, "close": 102, "volume": 1000}
        self.assertIsNotNone(validate_candle_row(row))

    def test_nan_close_rejected(self):
        row = {"ticker": "X", "timestamp": datetime(2026, 1, 1), "open": 100, "high": 105, "low": 95, "close": float("nan"), "volume": 1000}
        self.assertIsNotNone(validate_candle_row(row))

    def test_infinite_high_rejected(self):
        row = {"ticker": "X", "timestamp": datetime(2026, 1, 1), "open": 100, "high": float("inf"), "low": 95, "close": 102, "volume": 1000}
        self.assertIsNotNone(validate_candle_row(row))

    def test_negative_volume_rejected(self):
        row = {"ticker": "X", "timestamp": datetime(2026, 1, 1), "open": 100, "high": 105, "low": 95, "close": 102, "volume": -5}
        self.assertIsNotNone(validate_candle_row(row))

    def test_malformed_timestamp_rejected(self):
        row = {"ticker": "X", "timestamp": "not-a-date", "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        self.assertIsNotNone(validate_candle_row(row))

    def test_none_timestamp_rejected(self):
        row = {"ticker": "X", "timestamp": None, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        self.assertIsNotNone(validate_candle_row(row))


class TestPromote1DCandles(unittest.TestCase):
    def test_valid_staged_candle_is_promoted(self):
        db = _make_session()
        _stage(db, "RELIANCE", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        result = promote_1d_candles(db)
        self.assertEqual(result.promoted, 1)
        self.assertEqual(result.staged_rows_seen, 1)
        self.assertEqual(_production_count(db, "RELIANCE"), 1)

    def test_invalid_ohlcv_is_rejected_not_promoted(self):
        db = _make_session()
        _stage(db, "BADSTOCK", datetime(2026, 1, 1), 100, 90, 110, 102, 1000)  # high < low
        result = promote_1d_candles(db)
        self.assertEqual(result.promoted, 0)
        self.assertEqual(result.invalid_rejected, 1)
        self.assertEqual(_production_count(db, "BADSTOCK"), 0)
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0].ticker, "BADSTOCK")

    def test_malformed_timestamp_row_is_rejected_gracefully(self):
        """A staged row with a timestamp that doesn't parse (e.g. truncated
        or corrupted during fetch) must be rejected, not crash the whole
        promotion run or silently coerce to some arbitrary date. Production's
        real staging table (CREATE TABLE LIKE candles INCLUDING ALL) inherits
        NOT NULL constraints on open/high/low/close, so a NULL price can
        never even reach staging -- a bad timestamp string is the realistic
        malformed-row scenario for promotion to defend against."""
        db = _make_session()
        db.execute(text("""
            INSERT INTO candles_migration (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
            VALUES ('WEIRD', '1D', 'not-a-real-timestamp', 100, 105, 95, 102, 1000, 1, 'ANGELONE', 1)
        """))
        db.commit()
        result = promote_1d_candles(db)
        self.assertEqual(result.promoted, 0)
        self.assertEqual(result.invalid_rejected, 1)
        self.assertIn("timestamp", result.rejected[0].reason)

    def test_existing_production_row_is_preserved_not_overwritten(self):
        """The core safety guarantee: if production already has a row for
        (ticker, timeframe, timestamp), a DIFFERENT staged value for that
        same key must NOT change production's existing values."""
        db = _make_session()
        db.add(Candle(ticker="RELIANCE", timeframe="1D", timestamp=datetime(2026, 1, 1),
                       open=999, high=999, low=999, close=999, volume=999,
                       is_completed=True, data_source="LIVE", is_backfilled=False))
        db.commit()

        # Staging has a DIFFERENT (e.g. corrupted or re-fetched) value for the same key.
        _stage(db, "RELIANCE", datetime(2026, 1, 1), 1, 1, 1, 1, 1)

        result = promote_1d_candles(db)
        self.assertEqual(result.promoted, 0)
        self.assertEqual(result.already_in_production, 1)

        prod = db.query(Candle).filter(Candle.ticker == "RELIANCE", Candle.timeframe == "1D").one()
        self.assertEqual(prod.close, 999, "existing production value must be untouched")
        self.assertEqual(prod.data_source, "LIVE")

    def test_duplicate_staged_rows_are_harmless(self):
        """Running promotion is safe even if the staging table itself somehow
        contains the exact same (ticker, timestamp) twice."""
        db = _make_session()
        _stage(db, "TCS", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        _stage(db, "TCS", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        result = promote_1d_candles(db)
        self.assertEqual(_production_count(db, "TCS"), 1, "must not create two production rows for the same key")

    def test_repeated_promotion_is_idempotent(self):
        """Running the exact same promotion twice must not create duplicates
        or change anything the second time -- proves crash-safe resumability
        without any extra tracking state."""
        db = _make_session()
        _stage(db, "INFY", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        _stage(db, "INFY", datetime(2026, 1, 2), 102, 108, 100, 106, 1200)

        first = promote_1d_candles(db)
        self.assertEqual(first.promoted, 2)

        second = promote_1d_candles(db)
        self.assertEqual(second.promoted, 0)
        self.assertEqual(second.already_in_production, 2)
        self.assertEqual(_production_count(db, "INFY"), 2, "no duplicates after re-running")

    def test_partial_migration_can_resume_safely(self):
        """Simulates a crash after only some staged rows were promoted: a
        fresh promote_1d_candles() call must complete the remaining rows
        without disturbing the ones already promoted."""
        db = _make_session()
        for day in range(1, 6):
            _stage(db, "HDFC", datetime(2026, 1, day), 100 + day, 105 + day, 95 + day, 102 + day, 1000)

        # Simulate a crash: manually promote only the first 2 rows, as if the
        # process died mid-run after committing that much.
        db.add(Candle(ticker="HDFC", timeframe="1D", timestamp=datetime(2026, 1, 1), open=101, high=106, low=96, close=103, volume=1000, is_completed=True, data_source="ANGELONE", is_backfilled=True))
        db.add(Candle(ticker="HDFC", timeframe="1D", timestamp=datetime(2026, 1, 2), open=102, high=107, low=97, close=104, volume=1000, is_completed=True, data_source="ANGELONE", is_backfilled=True))
        db.commit()

        result = promote_1d_candles(db)
        self.assertEqual(result.promoted, 3, "only the 3 not-yet-promoted rows should be inserted")
        self.assertEqual(result.already_in_production, 2)
        self.assertEqual(_production_count(db, "HDFC"), 5, "all 5 days present, none duplicated")

    def test_only_1D_timeframe_is_promoted(self):
        """Staging may in principle contain other timeframes (e.g. a stray
        partial run of another tier) -- promote_1d_candles must never touch
        them, only timeframe='1D' rows."""
        db = _make_session()
        _stage(db, "WIPRO", datetime(2026, 1, 1), 100, 105, 95, 102, 1000, timeframe="1D")
        _stage(db, "WIPRO", datetime(2026, 1, 1, 9, 15), 50, 51, 49, 50, 200, timeframe="5m")

        result = promote_1d_candles(db)
        self.assertEqual(result.staged_rows_seen, 1, "the 5m staged row must not even be counted")
        self.assertEqual(_production_count(db, "WIPRO", timeframe="1D"), 1)
        self.assertEqual(_production_count(db, "WIPRO", timeframe="5m"), 0)

    def test_ticker_identity_is_not_confused_across_rows(self):
        """Two different tickers' rows in the same batch must never bleed
        into each other's production rows."""
        db = _make_session()
        _stage(db, "AAA", datetime(2026, 1, 1), 10, 11, 9, 10, 100)
        _stage(db, "BBB", datetime(2026, 1, 1), 20, 21, 19, 20, 200)
        promote_1d_candles(db)
        aaa = db.query(Candle).filter(Candle.ticker == "AAA", Candle.timeframe == "1D").one()
        bbb = db.query(Candle).filter(Candle.ticker == "BBB", Candle.timeframe == "1D").one()
        self.assertEqual(aaa.close, 10)
        self.assertEqual(bbb.close, 20)

    def test_mixed_batch_valid_and_invalid_rows_partial_success(self):
        """One bad row in a batch must not block the good rows in the same
        batch from being promoted."""
        db = _make_session()
        _stage(db, "GOOD", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        _stage(db, "BAD", datetime(2026, 1, 1), 100, 90, 110, 102, 1000)  # invalid OHLC
        result = promote_1d_candles(db)
        self.assertEqual(result.promoted, 1)
        self.assertEqual(result.invalid_rejected, 1)
        self.assertEqual(_production_count(db, "GOOD"), 1)
        self.assertEqual(_production_count(db, "BAD"), 0)

    def test_transaction_rolls_back_on_unexpected_promotion_failure(self):
        """A genuine unexpected failure (not a harmless duplicate-key
        collision -- those are handled separately, see the IntegrityError
        fallback) must roll back and propagate, not silently swallow the
        error or leave a half-committed batch."""
        db = _make_session()
        _stage(db, "CRASHY", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)

        from unittest.mock import patch
        with patch.object(db, "bulk_insert_mappings", side_effect=RuntimeError("simulated DB failure")):
            with self.assertRaises(RuntimeError):
                promote_1d_candles(db)

        # Nothing was promoted -- the failure must not leave a partial commit.
        self.assertEqual(_production_count(db, "CRASHY"), 0)

    def test_empty_staging_table_is_a_clean_no_op(self):
        db = _make_session()
        result = promote_1d_candles(db)
        self.assertEqual(result, PromotionResult())


class TestReconcile1DPromotion(unittest.TestCase):
    def test_reconciliation_reports_matched_tickers(self):
        db = _make_session()
        _stage(db, "MATCHED", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        promote_1d_candles(db)

        report = reconcile_1d_promotion(db)
        self.assertEqual(report.tickers_checked, 1)
        self.assertEqual(report.tickers_fully_matched, 1)
        self.assertEqual(report.mismatched_tickers, [])

    def test_reconciliation_flags_a_ticker_that_failed_to_promote(self):
        """If promotion is somehow incomplete (e.g. an earlier bug), the
        reconciliation pass must surface it rather than silently pass."""
        db = _make_session()
        _stage(db, "ORPHAN", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        # Deliberately do NOT call promote_1d_candles -- staging has data,
        # production does not.
        report = reconcile_1d_promotion(db)
        self.assertIn("ORPHAN", report.mismatched_tickers)

    def test_reconciliation_excludes_invalid_staging_rows_from_the_valid_count(self):
        db = _make_session()
        _stage(db, "BADROW", datetime(2026, 1, 1), 100, 90, 110, 102, 1000)  # invalid
        report = reconcile_1d_promotion(db)
        self.assertEqual(report.staging_invalid_rows, 1)
        self.assertEqual(report.staging_valid_rows, 0)
        self.assertNotIn("BADROW", report.mismatched_tickers, "an all-invalid ticker has nothing valid to reconcile")

    def test_reconciliation_uses_one_batched_query_not_one_per_ticker(self):
        # DB-06 regression: the production-side lookup used to run once per
        # staged ticker (N+1). Must now be a single query regardless of how
        # many tickers are being reconciled.
        db = _make_session()
        for i in range(5):
            _stage(db, f"T{i}", datetime(2026, 1, 1), 100, 105, 95, 102, 1000)
        promote_1d_candles(db)

        original_execute = db.execute
        call_log = []

        def counting_execute(stmt, *args, **kwargs):
            call_log.append(str(stmt))
            return original_execute(stmt, *args, **kwargs)

        db.execute = counting_execute
        try:
            report = reconcile_1d_promotion(db)
        finally:
            db.execute = original_execute

        prod_lookup_calls = [c for c in call_log if "FROM candles WHERE" in c and "candles_migration" not in c]
        self.assertEqual(len(prod_lookup_calls), 1,
                          "must fetch all tickers' production rows in ONE query, not one per ticker")
        self.assertEqual(report.tickers_checked, 5)
        self.assertEqual(report.tickers_fully_matched, 5)


if __name__ == "__main__":
    unittest.main()
