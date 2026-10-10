"""Regression tests for the /api/all-stocks search universe.

Root cause: `/api/all-stocks` returned only rows with `is_active == True`. The
yfinance failure tracker flips `is_active` to False for any ticker whose symbol
merely failed to resolve, so a genuinely listed stock (e.g. MEESHO) disappeared
from the search bar entirely -- and a company with no *stored* history either
(e.g. TAPARIA / Taparia Tools Ltd., 0 rows in stock_data) could never come back.

Fix: the search universe is EVERY instrument in `stock_metadata`, collapsed to
one row per ticker (NSE preferred), built with a single query. Being searchable
is a metadata concern, not a data-availability one. These tests run against an
in-memory SQLite database -- no Postgres and no live providers.
"""
import json
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import main
import models


def _make_session():
    """In-memory SQLite session for just the two tables involved, plus a list
    that captures every SQL statement actually emitted."""
    engine = create_engine("sqlite://")
    models.StockMetadata.__table__.create(engine)
    models.StockData.__table__.create(engine)
    stmts = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        stmts.append(statement)

    session = sessionmaker(bind=engine)()
    return session, stmts


def _md(ticker, name, exchange, active, base=100.0):
    return models.StockMetadata(
        ticker=ticker, name=name, exchange=exchange,
        is_active=active, is_premium=False, base_price=base,
    )


def _sd(ticker):
    return models.StockData(
        ticker=ticker, date=date(2026, 1, 2),
        open=1.0, high=1.0, low=1.0, close=1.0, adj_close=1.0, volume=1,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        main._all_stocks_cache_bytes = None
        main._all_stocks_cache_ts = 0.0

    def tearDown(self):
        main._all_stocks_cache_bytes = None
        main._all_stocks_cache_ts = 0.0

    @staticmethod
    def _call(db):
        with patch.object(main, "_get_all_market_prices", return_value={}):
            resp = main.get_all_stocks(db)
        return json.loads(resp.body.decode())


class EligibleUniverseTests(_Base):
    def test_inactive_ticker_with_history_is_searchable(self):
        db, _ = _make_session()
        db.add_all([
            _md("MEESHO", "Meesho Limited", "NSE", False),
            _md("MEESHO", "Meesho Limited", "BSE", False),
        ])
        db.add(_sd("MEESHO"))
        db.commit()
        self.assertIn("MEESHO", [r["ticker"] for r in self._call(db)])

    def test_active_ticker_without_history_is_searchable(self):
        db, _ = _make_session()
        db.add(_md("NEWCO", "New Co", "NSE", True))
        db.commit()
        self.assertIn("NEWCO", [r["ticker"] for r in self._call(db)])

    def test_inactive_metadata_only_is_searchable(self):
        """A genuinely listed company must stay searchable even when it is
        flagged inactive AND has no stored history (e.g. TAPARIA / Taparia
        Tools Ltd.: 0 rows in stock_data, 0 candles)."""
        db, _ = _make_session()
        db.add(_md("TAPARIA", "Taparia Tools Ltd.", "BSE", False))
        db.commit()
        self.assertIn("TAPARIA", [r["ticker"] for r in self._call(db)])

    def test_nse_bse_duplicates_collapse_to_one_row(self):
        db, _ = _make_session()
        db.add_all([
            _md("TCS", "Tata Consultancy Services", "BSE", True),
            _md("TCS", "Tata Consultancy Services", "NSE", True),
        ])
        db.commit()
        out = self._call(db)
        self.assertEqual([r["ticker"] for r in out].count("TCS"), 1)
        self.assertEqual(
            [r for r in out if r["ticker"] == "TCS"][0]["exchange"], "NSE")

    def test_response_shape_is_backward_compatible(self):
        db, _ = _make_session()
        db.add(_md("RELIANCE", "Reliance Industries", "NSE", True))
        db.commit()
        row = self._call(db)[0]
        for key in ("ticker", "name", "logo", "exchange", "basePrice",
                    "change", "changePercent", "change_pct"):
            self.assertIn(key, row)

    def test_universe_is_a_single_query_no_per_ticker_roundtrips(self):
        db, stmts = _make_session()
        for i in range(25):
            db.add(_md(f"TK{i}", f"Name {i}", "NSE", True))
        db.commit()
        self._call(db)
        metadata_selects = [s for s in stmts if "FROM stock_metadata" in s]
        self.assertEqual(len(metadata_selects), 1,
                         "the universe must be built with a single query")
        self.assertNotIn("stock_data", metadata_selects[0],
                         "searchability must not depend on stored history")

    def test_cached_response_avoids_a_second_query(self):
        db, stmts = _make_session()
        db.add(_md("TCS", "Tata Consultancy Services", "NSE", True))
        db.commit()
        self._call(db)
        first = len(stmts)
        self._call(db)
        self.assertEqual(len(stmts), first, "second call must hit the cache")

    def test_query_failure_falls_back_to_active_only(self):
        """If the eligible-universe query fails, fall back to active-only
        instead of returning nothing."""
        db, _ = _make_session()
        db.add_all([
            _md("TCS", "Tata Consultancy Services", "NSE", True),
            _md("GHOST", "Ghost Ltd", "NSE", False),
        ])
        db.commit()
        real_query = db.query
        calls = {"n": 0}

        class _Flaky:
            def query(self, *a, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("simulated failure")
                return real_query(*a, **k)

        with patch.object(main, "_get_all_market_prices", return_value={}):
            resp = main.get_all_stocks(_Flaky())
        tickers = [r["ticker"] for r in json.loads(resp.body.decode())]
        self.assertIn("TCS", tickers)
        self.assertNotIn("GHOST", tickers)

    def test_no_duplicate_tickers_in_payload(self):
        db, _ = _make_session()
        db.add_all([
            _md("X", "X Ltd", "NSE", True),
            _md("X", "X Ltd", "BSE", True),
            _md("Y", "Y Ltd", "BSE", False),
        ])
        db.add(_sd("Y"))
        db.commit()
        tickers = [r["ticker"] for r in self._call(db)]
        self.assertEqual(len(tickers), len(set(tickers)))


class StocksVersionTests(_Base):
    def test_version_carries_the_universe_revision(self):
        """A deploy that changes the universe must invalidate every client's
        localStorage stock list immediately (not at the next 18:00 IST)."""
        version = main.get_stocks_version()["version"]
        self.assertTrue(version.endswith("|u3"), version)


if __name__ == "__main__":
    unittest.main()
