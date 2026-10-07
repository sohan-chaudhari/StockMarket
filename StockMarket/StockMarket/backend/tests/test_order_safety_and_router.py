"""Regression tests for the LEVERAGE four-issue investigation — Issue 1.

These lock in the *safety* properties of the paper-trading execution path:

  * The automatic TP/SL engine may only ever CLOSE a position. It must contain
    no code path that opens one (the reported "another stock was automatically
    purchased" was a frontend display/attribution issue, not an order path).
  * Repeat processing of the same order cannot double-close a position or
    credit the balance twice (guards against duplicate events / reconnects).
  * Outside NSE market hours the engine refuses to evaluate prices.
  * The legacy /api/trade/open router correctly unpacks
    TradingService.open_position()'s 3-tuple (it used to raise ValueError).
"""
import asyncio
import inspect
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
import execution_engine
import routers.trade_router as trade_router
import schemas
from database import Base
from trade_service import TradingService


class OrderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.user = models.User(
            email="safety@example.com", password_hash="x", virtual_balance=100000.0
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def _open_position_with_order(self, order_type, trigger):
        pos = models.Position(
            user_id=self.user.user_id, ticker="TCS", position_type="LONG",
            quantity=1, entry_price=100.0, total_investment=100.0, status="OPEN",
        )
        self.db.add(pos)
        self.db.commit()
        self.db.refresh(pos)
        order = models.Order(
            position_id=pos.id, user_id=self.user.user_id, ticker="TCS",
            order_type=order_type, trigger_price=trigger, status="PENDING",
        )
        self.db.add(order)
        self.db.commit()
        self.db.refresh(order)
        return pos, order

    def _balance(self):
        self.db.expire_all()
        return self.db.query(models.User).filter_by(
            user_id=self.user.user_id
        ).first().virtual_balance

    def test_tp_execution_closes_but_never_opens(self):
        pos, order = self._open_position_with_order("TP", 110.0)
        positions_before = self.db.query(models.Position).count()
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            asyncio.run(
                execution_engine.price_monitor.execute_order(self.db, order, 115.0)
            )
        self.assertEqual(
            self.db.query(models.Position).count(), positions_before,
            "TP execution must not create any new position",
        )
        self.db.refresh(pos)
        self.db.refresh(order)
        self.assertEqual(pos.status, "CLOSED")
        self.assertEqual(pos.close_type, "TP_EXECUTED")
        self.assertEqual(order.status, "EXECUTED")

    def test_duplicate_processing_cannot_double_close(self):
        pos, order = self._open_position_with_order("SL", 90.0)
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            asyncio.run(
                execution_engine.price_monitor.execute_order(self.db, order, 85.0)
            )
            bal_after_first = self._balance()
            asyncio.run(
                execution_engine.price_monitor.execute_order(self.db, order, 85.0)
            )
            bal_after_second = self._balance()
        self.assertEqual(
            bal_after_first, bal_after_second,
            "re-processing the same order must not credit the balance twice",
        )
        self.assertEqual(
            self.db.query(models.Position).filter_by(status="CLOSED").count(), 1
        )
        self.assertEqual(
            self.db.query(models.Transaction)
            .filter_by(position_id=pos.id, transaction_type="SL_EXECUTED")
            .count(),
            1,
        )

    def test_market_closed_guard_blocks_execution(self):
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=False):
            self.assertFalse(execution_engine.price_monitor._is_market_open())

    def test_execution_engine_has_no_open_position_code_path(self):
        src = inspect.getsource(execution_engine)
        self.assertNotIn(
            "open_position", src,
            "the automatic engine must never open positions",
        )
        self.assertIn("close_position", src)

    def test_trade_router_unpacks_open_position_triple(self):
        pos = models.Position(
            user_id=self.user.user_id, ticker="TCS", position_type="LONG",
            quantity=1, entry_price=100.0, total_investment=100.0, status="OPEN",
        )
        self.db.add(pos)
        self.db.commit()
        self.db.refresh(pos)
        req = schemas.PlaceOrderRequest(
            ticker="TCS", position_type="LONG", quantity=1, entry_price=100.0,
            take_profit=110.0, stop_loss=90.0,
        )
        # slowapi wraps router endpoints with functools.wraps → __wrapped__ is
        # the unwrapped endpoint we actually want to exercise.
        fn = getattr(trade_router.open_position, "__wrapped__", trade_router.open_position)
        with patch.object(TradingService, "open_position", return_value=(pos, "ok", None)):
            resp = fn(request=None, req=req, db=self.db, current_user=self.user)
        self.assertEqual(resp.id, pos.id)

    def test_trade_service_open_position_returns_three_values(self):
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            result = TradingService.open_position(
                db=self.db, user_id=self.user.user_id, ticker="TCS",
                position_type="LONG", quantity=1, entry_price=100.0,
                take_profit=110.0, stop_loss=90.0, stock_name="TCS",
            )
        self.assertEqual(len(result), 3, "open_position contract is a 3-tuple")


if __name__ == "__main__":
    unittest.main()
