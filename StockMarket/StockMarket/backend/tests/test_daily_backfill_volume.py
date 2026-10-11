"""Regression tests: synthesized 1D candles were written with volume = 0.

Both writers aggregated stored 5-minute candles into 1D rows but hardcoded the
volume column to the literal 0:

  * main._backfill_daily_from_5m  (single set-based INSERT ... SELECT)
  * main._backfill_index_daily    (per-index loop; 5m-derived fallback path)

Because main._refresh_db_baseline_sync takes the LATEST 1D row as the movers
baseline, that 0 became the displayed "Volume: 0" on Top Gainers/Losers.

These tests prove the volume now comes from the sum of the stored 5m volumes and
that a genuinely zero-sum day stays 0. No network, no provider, no real DB.
"""
import re
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text

import main


class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """Minimal Session stand-in that routes execute() by SQL shape."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []          # (sql, params)
        self.committed = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        return self._handler(sql, params)

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        self.closed += 1


def _split_top_level(exprs_sql):
    """Split a SELECT list on top-level commas (parenthesis-aware)."""
    out, depth, cur = [], 0, ""
    for ch in exprs_sql:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _parse_insert_select(sql):
    """Return (insert_columns, select_expressions) for INSERT INTO ... SELECT ..."""
    m = re.search(r"INSERT\s+INTO\s+candles\s*\(([^)]*)\)", sql, re.S | re.I)
    cols = [c.strip() for c in m.group(1).split(",")]
    m2 = re.search(r"\bSELECT\b(.*?)\bFROM\b", sql, re.S | re.I)
    return cols, _split_top_level(m2.group(1))


class DailyFrom5mSqlContractTests(unittest.TestCase):
    """The set-based 5m -> 1D writer must aggregate volume, not hardcode 0."""

    def setUp(self):
        self.sess = _FakeSession(lambda sql, params: _Result([], rowcount=7))
        self._p = patch("main.database.SessionLocal", lambda: self.sess)
        self._p.start()
        self.addCleanup(self._p.stop)

    def _sql(self):
        main._backfill_daily_from_5m(days_back=10)
        self.assertTrue(self.sess.calls, "no SQL was executed")
        return self.sess.calls[0][0]

    def test_volume_column_receives_the_5m_volume_sum(self):
        cols, exprs = _parse_insert_select(self._sql())
        self.assertEqual(len(cols), len(exprs), "column list / select list misaligned")
        vi = cols.index("volume")
        expr = exprs[vi].replace(" ", "")
        self.assertIn("sum(c.volume)", expr)
        self.assertIn("coalesce", expr)

    def test_volume_is_not_a_hardcoded_zero(self):
        cols, exprs = _parse_insert_select(self._sql())
        self.assertNotEqual(exprs[cols.index("volume")].replace(" ", ""), "0")

    def test_ohlc_and_metadata_are_unchanged(self):
        cols, exprs = _parse_insert_select(self._sql())
        by_col = dict(zip(cols, [e.replace(" ", "") for e in exprs]))
        self.assertEqual(by_col["timeframe"], "'1D'")
        self.assertEqual(by_col["data_source"], "'BACKFILL'")
        self.assertEqual(by_col["is_backfilled"], "false")
        self.assertEqual(by_col["is_completed"], "true")
        # OHLC unchanged: first 5m open, max high, min low, last 5m close
        self.assertIn("array_agg(c.open", by_col["open"])
        self.assertEqual(by_col["high"], "max(c.high)")
        self.assertEqual(by_col["low"], "min(c.low)")
        self.assertIn("array_agg(c.close", by_col["close"])

    def test_existing_1d_row_precedence_and_conflict_handling_preserved(self):
        sql = self._sql()
        self.assertIn("NOT EXISTS", sql)                       # don't clobber stored 1D
        self.assertIn("ON CONFLICT ON CONSTRAINT uix_candle_key DO NOTHING", sql)
        self.assertIn("date_trunc('day', c.timestamp)", sql)   # day alignment preserved

    def test_returns_inserted_rowcount(self):
        self.assertEqual(main._backfill_daily_from_5m(days_back=10), 7)


class IndexDaily5mFallbackVolumeTests(unittest.TestCase):
    """_backfill_index_daily's 5m-derived path must carry the 5m volume sum."""

    def _run(self, agg_rows, angelone_candles):
        self.sess = _FakeSession(self._handler_for(agg_rows))
        self.hist = MagicMock()
        self.hist.is_logged_in = True
        self.hist.get_historical_candles.return_value = angelone_candles
        with patch("main.database.SessionLocal", lambda: self.sess), \
             patch("main.historical_service", self.hist):
            inserted = main._backfill_index_daily(days_back=20)
        return inserted

    def _handler_for(self, agg_rows):
        def handler(sql, params):
            if "SELECT timestamp::date FROM candles" in sql:   # `have` probe
                return _Result([])
            if "array_agg(open" in sql:                        # 5m aggregate
                return _Result(agg_rows)
            if "INSERT INTO candles" in sql:
                return _Result([], rowcount=1)
            return _Result([])
        return handler

    def _insert_params(self):
        return [p for (sql, p) in self.sess.calls if "INSERT INTO candles" in sql]

    def test_5m_sum_becomes_the_daily_volume(self):
        # 10-09 has real 5m volume; 10-08 is a genuinely zero-volume day
        self._run(
            agg_rows=[(date(2026, 10, 9), 10.0, 12.0, 9.0, 11.0, 12345),
                      (date(2026, 10, 8), 5.0, 6.0, 4.0, 5.5, 0)],
            angelone_candles=[],
        )
        params = self._insert_params()
        self.assertTrue(params)
        vols = {p["v"] for p in params}
        self.assertIn(12345, vols, "nonzero 5m sum must become the daily volume")
        self.assertIn(0, vols, "a zero 5m sum must stay zero")
        self.assertTrue(all(p["src"] == "BACKFILL" for p in params))
        self.assertTrue(all(p["c"] > 0 for p in params))

    def test_angelone_official_candle_uses_provider_volume(self):
        self._run(
            agg_rows=[],
            angelone_candles=[{"timestamp": datetime(2026, 10, 9),
                               "open": 10.0, "high": 12.0, "low": 9.0,
                               "close": 11.0, "volume": 777}],
        )
        params = self._insert_params()
        self.assertTrue(params)
        self.assertEqual({p["v"] for p in params}, {777})
        self.assertEqual({p["src"] for p in params}, {"ANGELONE"})


class AggregationContractTests(unittest.TestCase):
    """SQLite translation of the writer's contract (real SQL is Postgres-only).

    Demonstrates the three properties the fix relies on: a nonzero 5m day sums,
    an all-zero 5m day stays 0, and a day that already has a 1D row is skipped.
    """

    def test_nonzero_sums_and_zero_stays_zero(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE c5 (ticker TEXT, day TEXT, volume INTEGER)"))
            conn.execute(text(
                "INSERT INTO c5 VALUES "
                "('A','2026-10-09',100),('A','2026-10-09',200),('A','2026-10-09',300),"
                "('B','2026-10-09',0),('B','2026-10-09',0)"))
            rows = dict(conn.execute(text(
                "SELECT ticker, coalesce(sum(volume), 0) FROM c5 GROUP BY ticker")).fetchall())
        self.assertEqual(rows["A"], 600)   # reconstructed, not hardcoded 0
        self.assertEqual(rows["B"], 0)     # genuine zero preserved

    def test_existing_1d_row_is_not_overwritten(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE c5 (ticker TEXT, day TEXT, volume INTEGER)"))
            conn.execute(text("CREATE TABLE d1 (ticker TEXT, day TEXT, volume INTEGER)"))
            conn.execute(text("INSERT INTO c5 VALUES ('A','2026-10-09',500)"))
            conn.execute(text("INSERT INTO d1 VALUES ('A','2026-10-09',999)"))  # real row
            conn.execute(text(
                "INSERT INTO d1 (ticker, day, volume) "
                "SELECT c.ticker, c.day, coalesce(sum(c.volume), 0) FROM c5 c "
                "WHERE NOT EXISTS (SELECT 1 FROM d1 d WHERE d.ticker = c.ticker AND d.day = c.day) "
                "GROUP BY c.ticker, c.day"))
            rows = dict(conn.execute(text("SELECT ticker, volume FROM d1")).fetchall())
        self.assertEqual(rows["A"], 999, "stored 1D row must win over a synthesized one")


if __name__ == "__main__":
    unittest.main()
