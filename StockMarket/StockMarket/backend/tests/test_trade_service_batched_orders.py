"""DB-06 regression: TradingService.get_orders_for_positions() must return
the exact same TP/SL grouping as calling get_position_orders() once per
position -- the open/closed-positions endpoints used to do that in a loop
(N+1 queries), this batches it into one query for all positions at once.

Uses a real in-memory SQLite database with the actual ORM models (matching
the existing TradingService test convention in test_amo_queue_system.py),
not mocks -- so the exact query TradingService issues is exercised.
"""
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from trade_service import TradingService


class TestGetOrdersForPositions(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.user = models.User(email="t@example.com", password_hash="x", virtual_balance=1000000.0)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def _open(self, ticker, tp=None, sl=None):
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            position, msg, order = TradingService.open_position(
                db=self.db, user_id=self.user.user_id, ticker=ticker,
                position_type="LONG", quantity=1, entry_price=100.0,
                take_profit=tp, stop_loss=sl, stock_name=ticker,
            )
        self.assertIsNotNone(position, msg)
        return position

    def test_batched_result_matches_per_position_lookup(self):
        p1 = self._open("AAA", tp=110.0, sl=90.0)
        p2 = self._open("BBB", tp=120.0, sl=95.0)
        p3 = self._open("CCC")  # no TP/SL

        batched = TradingService.get_orders_for_positions(self.db, [p1.id, p2.id, p3.id])

        for p in (p1, p2, p3):
            individual = TradingService.get_position_orders(self.db, p.id)
            self.assertEqual(
                batched[p.id]["TP"].trigger_price if batched[p.id]["TP"] else None,
                individual["TP"].trigger_price if individual["TP"] else None,
            )
            self.assertEqual(
                batched[p.id]["SL"].trigger_price if batched[p.id]["SL"] else None,
                individual["SL"].trigger_price if individual["SL"] else None,
            )

    def test_empty_position_list_returns_empty_dict(self):
        self.assertEqual(TradingService.get_orders_for_positions(self.db, []), {})

    def test_position_with_no_orders_gets_none_placeholders(self):
        p = self._open("DDD")
        batched = TradingService.get_orders_for_positions(self.db, [p.id])
        self.assertEqual(batched[p.id], {"TP": None, "SL": None})

    def test_orders_from_other_positions_do_not_cross_contaminate(self):
        p1 = self._open("EEE", tp=111.0)
        p2 = self._open("FFF", tp=222.0)

        batched = TradingService.get_orders_for_positions(self.db, [p1.id, p2.id])

        self.assertEqual(batched[p1.id]["TP"].trigger_price, 111.0)
        self.assertEqual(batched[p2.id]["TP"].trigger_price, 222.0)
        self.assertIsNone(batched[p1.id]["SL"])
        self.assertIsNone(batched[p2.id]["SL"])


if __name__ == "__main__":
    unittest.main()
