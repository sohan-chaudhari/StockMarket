import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import database
from models import Candle, StockData
from price_provider import PriceProvider, PriceSource

TICKER = "TEST0001"
FROZEN = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
FROZEN_TS = FROZEN.timestamp()
NOW = lambda: FROZEN


def mock_angel(entries):
    angel = MagicMock()
    angel.latest_ticks_lock = threading.Lock()
    angel.latest_ticks = dict(entries) or {}
    return angel


def mock_aggregator(currents):
    agg = MagicMock()
    current_default = currents.get(None, {})

    def _get_current(t):
        return currents.get(t, current_default)

    agg.get_current.side_effect = _get_current
    return agg


def make_provider(angel=None, aggregator=None, session_factory=None, market_open=True):
    return PriceProvider(
        angelone_service=angel,
        candle_aggregator=aggregator,
        session_factory=session_factory or (lambda: MagicMock()),
        market_open=lambda dt: market_open,
        now_fn=NOW,
    )


class CapturingSession:
    """Records how many query() calls each logical tier triggers and returns rows.

    BUG FOUND (real failure, not simulated): this mock originally routed
    purely by `__tablename__` -- "candles" -> completed_rows, "stock_data"
    -> daily_rows. That matched an OLDER architecture where the daily-close
    tier read from the separate `stock_data` table. It no longer does: per
    price_provider.py's `_resolve_tier_daily`, daily close is sourced from
    the SAME unified `candles` table as the completed-5m tier (filtered to
    `timeframe == "1D"`), because `stock_data` is dead/frozen legacy (only
    the retired `/api/stock-data` endpoint still reads it -- see main.py).
    Once both tiers query the same table, routing by table name alone can
    no longer tell them apart, so the daily tier's mocked query silently
    returned `completed_rows` (usually empty) instead of `daily_rows`,
    making get_price()/get_prices() wrongly resolve to nothing whenever
    only the daily tier had data. Fixed by routing on QUERY SHAPE instead:
    `_resolve_tier_completed` queries the mapped `Candle` class directly in
    one shot; `_resolve_tier_daily` always builds a column-expression
    subquery first (`query(Candle.ticker, func.max(...))`, not the mapped
    class) and THEN joins a `Candle` query against it -- that `.join(...)`
    call is the one and only path exclusive to the daily tier, so `_Q.join()`
    is what actually resolves to `daily_rows` below, matching how the real
    two-step subquery+join SQLAlchemy query is actually built.
    """

    def __init__(self, completed_rows=None, daily_rows=None):
        self.completed_rows = completed_rows or []
        self.daily_rows = daily_rows or []
        self.calls = []

    def query(self, *models):
        model = models[0] if models else None
        is_subquery_builder = len(models) > 1 or (model is not None and not hasattr(model, "__tablename__"))
        if is_subquery_builder:
            # Building the GROUP BY subquery is not itself a round trip --
            # no terminal (.all()/.first()) is ever called on it directly,
            # only .subquery() -- so nothing is recorded in self.calls here,
            # matching real SQLAlchemy (a Subquery is a SQL expression, not
            # an executed query).
            return _Q(None, session=self, kind="subquery")
        name = model.__tablename__ if hasattr(model, "__tablename__") else "other"
        rows = self.completed_rows if name == "candles" else (self.daily_rows if name == "stock_data" else [])
        return _Q(rows, session=self, kind=name)

    def rollback(self):
        pass

    def close(self):
        pass


class _Q:
    def __init__(self, rows, session=None, kind=None):
        self.rows = rows
        self._session = session
        self._kind = kind

    def filter(self, *a, **kw):
        return self

    def order_by(self, *a, **kw):
        return self

    def group_by(self, *a, **kw):
        return self

    def subquery(self):
        return _Sub()

    def join(self, *a, **kw):
        # Only _resolve_tier_daily ever calls .join() (against its own
        # subquery) -- this IS the daily tier's one real terminal query,
        # hitting the same `candles` table the completed tier queries
        # directly (see CapturingSession's docstring). Labeled "candles" to
        # keep existing round-trip-count assertions meaningful (it's a real
        # query against that table), distinguished from the completed
        # tier's own "candles" call only by when .all()/.first() fires.
        return _Q(self._session.daily_rows if self._session else [], session=self._session, kind="candles_daily")

    def all(self):
        if self._session is not None and self._kind is not None:
            self._session.calls.append(self._kind)
        return self.rows or []

    def first(self):
        if self._session is not None and self._kind is not None:
            self._session.calls.append(self._kind)
        return self.rows[0] if self.rows else None


class _Sub:
    def __init__(self):
        self.c = MagicMock()

    def filter(self, *a, **kw):
        return self

    def group_by(self, *a, **kw):
        return self

    def subquery(self):
        return self


class Row:
    def __init__(self, ticker, close, timestamp=None):
        self.ticker = ticker
        self.close = close
        self.timestamp = timestamp


class TestLiveTickTier(unittest.TestCase):
    def test_uses_fresh_live_tick(self):
        p = make_provider(
            angel=mock_angel({TICKER: {"current_price": 123.45, "_received_ts": FROZEN_TS}}),
            aggregator=mock_aggregator({}),
        )
        snap = p.get_price(TICKER)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.price, 123.45)
        self.assertEqual(snap.source, PriceSource.LIVE_TICK)

    def test_stale_live_tick_ignored(self):
        p = make_provider(
            angel=mock_angel({TICKER: {"current_price": 123.45, "_received_ts": FROZEN_TS - 60}}),
            aggregator=mock_aggregator({TICKER: {"5m": {"close": 200.0}}}),
        )
        snap = p.get_price(TICKER)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.price, 200.0)
        self.assertEqual(snap.source, PriceSource.FORMING_5M)

    def test_no_tick_entry_falls_through(self):
        p = make_provider(
            angel=mock_angel({}),
            aggregator=mock_aggregator({TICKER: {"5m": {"close": 99.0}}}),
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 99.0)


class TestForming5mTier(unittest.TestCase):
    def test_forming_5m_close_used(self):
        p = make_provider(aggregator=mock_aggregator({TICKER: {"5m": {"close": 88.5}}}))
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 88.5)
        self.assertEqual(snap.source, PriceSource.FORMING_5M)

    def test_forming_5m_missing_falls_to_completed(self):
        p = make_provider(
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(completed_rows=[Row(TICKER, 77.25)]),
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 77.25)
        self.assertEqual(snap.source, PriceSource.COMPLETED_5M)

    def test_forming_5m_negative_close_rejected(self):
        p = make_provider(
            aggregator=mock_aggregator({TICKER: {"5m": {"close": -1.0}}}),
            session_factory=lambda: CapturingSession(completed_rows=[Row(TICKER, 70.0)]),
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 70.0)

    def test_forming_5m_fresh_bucket_used(self):
        # TP-01: bucket "time" == now -> age 0, well under the threshold.
        p = make_provider(
            aggregator=mock_aggregator({TICKER: {"5m": {"close": 88.5, "time": FROZEN_TS}}}),
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 88.5)
        self.assertEqual(snap.source, PriceSource.FORMING_5M)

    def test_forming_5m_stale_bucket_rejected_falls_to_completed(self):
        # TP-01 regression: previously a forming candle from arbitrarily long
        # ago was returned as "current price" with no staleness check at
        # all. A bucket that hasn't advanced in far more than one 5m window
        # must now be rejected and fall through to the next tier.
        stale_time = FROZEN_TS - 3600  # 1 hour old, well past the 420s cap
        p = make_provider(
            aggregator=mock_aggregator({TICKER: {"5m": {"close": 88.5, "time": stale_time}}}),
            session_factory=lambda: CapturingSession(completed_rows=[Row(TICKER, 70.0)]),
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 70.0)
        self.assertEqual(snap.source, PriceSource.COMPLETED_5M)


class TestCompleted5mTier(unittest.TestCase):
    def test_completed_5m_from_db(self):
        p = make_provider(
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(completed_rows=[Row(TICKER, 66.5)]),
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 66.5)
        self.assertEqual(snap.source, PriceSource.COMPLETED_5M)

    def test_completed_5m_fresh_timestamp_used(self):
        # TP-01: timestamp captured live at test time -> age ~0.
        p = make_provider(
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(
                completed_rows=[Row(TICKER, 66.5, timestamp=database.get_ist_now())]
            ),
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 66.5)
        self.assertEqual(snap.source, PriceSource.COMPLETED_5M)

    def test_completed_5m_stale_timestamp_rejected(self):
        # TP-01 regression: an hour-old-or-more completed 5m candle must not
        # be usable as "current price" -- previously tier 3 had no
        # staleness check at all. market_open=True keeps tier 4 (daily
        # close) out of the picture, and no daily fallback rows exist, so a
        # correctly-rejected stale candle resolves to no price at all.
        p = make_provider(
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(
                completed_rows=[Row(TICKER, 66.5, timestamp=datetime(2000, 1, 1))]
            ),
            market_open=True,
        )
        snap = p.get_price(TICKER)
        self.assertIsNone(snap)


class TestDailyCloseTier(unittest.TestCase):
    def test_daily_close_suppressed_while_market_open(self):
        p = make_provider(
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(daily_rows=[Row(TICKER, 55.0)]),
            market_open=True,
        )
        self.assertIsNone(p.get_price(TICKER))

    def test_daily_close_used_when_market_closed(self):
        p = make_provider(
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(daily_rows=[Row(TICKER, 55.0)]),
            market_open=False,
        )
        snap = p.get_price(TICKER)
        self.assertEqual(snap.price, 55.0)
        self.assertEqual(snap.source, PriceSource.DAILY_CLOSE)


class TestBatching(unittest.TestCase):
    def test_get_prices_completed_tier_single_query(self):
        session = CapturingSession(completed_rows=[Row("A", 10.0), Row("B", 20.0), Row("C", 30.0)])
        p = make_provider(
            angel=mock_angel({}),
            aggregator=mock_aggregator({}),
            session_factory=lambda: session,
        )
        res = p.get_prices(["A", "B", "C"])
        self.assertEqual(len(res), 3)
        # Exactly one candles-table query for the whole batch, not one per ticker
        self.assertEqual(session.calls.count("candles"), 1)

    def test_get_prices_daily_single_query(self):
        session = CapturingSession(daily_rows=[Row("A", 10.0), Row("B", 20.0), Row("C", 30.0)])
        p = make_provider(
            angel=mock_angel({}),
            aggregator=mock_aggregator({}),
            session_factory=lambda: session,
            market_open=False,
        )
        res = p.get_prices(["A", "B", "C"])
        self.assertEqual(len(res), 3)
        # Tier 3 (completed 5m) always runs first and finds nothing (no
        # completed_rows configured) -- one round trip against `candles`.
        # Tier 4 (daily close) then resolves all three tickers in a SINGLE
        # additional round trip against the SAME `candles` table (subquery
        # build + join is one real query, not two -- see CapturingSession's
        # docstring). Daily close is sourced from the unified `candles`
        # table (timeframe='1D'), not the legacy `stock_data` table (dead;
        # only the retired /api/stock-data endpoint still reads it) -- this
        # replaces a stale assertion that expected a "stock_data" query,
        # which this code path never issues at all.
        self.assertEqual(session.calls.count("candles"), 1)
        self.assertEqual(session.calls.count("candles_daily"), 1)
        self.assertEqual(session.calls.count("stock_data"), 0)


class TestImmutableSnapshot(unittest.TestCase):
    def test_snapshot_is_frozen(self):
        from dataclasses import FrozenInstanceError

        p = make_provider(aggregator=mock_aggregator({TICKER: {"5m": {"close": 5.0}}}))
        snap = p.get_price(TICKER)
        with self.assertRaises(FrozenInstanceError):
            snap.price = 999.9


class TestPipelineOrderAndEdge(unittest.TestCase):
    def test_all_tiers_empty_returns_none(self):
        p = make_provider(
            angel=mock_angel({}),
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(completed_rows=[], daily_rows=[]),
            market_open=False,
        )
        self.assertIsNone(p.get_price(TICKER))

    def test_get_prices_skips_missing(self):
        p = make_provider(
            angel=mock_angel({TICKER: {"current_price": 5.0, "_received_ts": FROZEN_TS}}),
            aggregator=mock_aggregator({}),
            session_factory=lambda: CapturingSession(completed_rows=[], daily_rows=[]),
        )
        res = p.get_prices([TICKER, "MISSING"])
        self.assertIn(TICKER, res)
        self.assertEqual(res[TICKER].price, 5.0)
        self.assertNotIn("MISSING", res)


if __name__ == "__main__":
    unittest.main()