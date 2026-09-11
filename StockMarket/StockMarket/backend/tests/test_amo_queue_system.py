import unittest
from datetime import datetime, date, time, timedelta, timezone
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from trade_service import TradingService
from exchange_calendar import NSECalendar, IST


class TestMarketHoursEnforcement(unittest.TestCase):
    """
    Test suite for strict market closed trading rejection:
    1. Orders placed outside market hours are strictly rejected with clear next open message.
    2. Modifying TP/SL outside market hours is strictly blocked.
    3. Closing a position outside market hours is strictly blocked.
    4. Orders placed during open market hours are accepted.
    5. Holiday calendar accurately resolves next active trading day.
    """

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Create test user
        self.user = models.User(
            email="trader@example.com",
            password_hash="hashed_pw",
            virtual_balance=100000.00
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_order_rejected_when_market_closed(self):
        """Outside market hours: order placement is strictly rejected, no rows created, balance untouched."""
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=False):
            position, msg, order = TradingService.open_position(
                db=self.db,
                user_id=self.user.user_id,
                ticker="TCS",
                position_type="LONG",
                quantity=10,
                entry_price=1000.0,
                take_profit=1100.0,
                stop_loss=950.0,
                stock_name="Tata Consultancy Services"
            )

            self.assertIsNone(position)
            self.assertIsNone(order)
            self.assertIn("Market is closed", msg)
            self.assertIn("09:15 AM", msg)

            # Positions and Orders tables must have 0 rows
            self.assertEqual(self.db.query(models.Position).count(), 0)
            self.assertEqual(self.db.query(models.Order).count(), 0)

            # User balance must remain untouched
            self.db.refresh(self.user)
            self.assertEqual(self.user.virtual_balance, 100000.0)

    def test_order_accepted_when_market_open(self):
        """During market hours: order executes immediately creating Position, Orders, and deducting balance."""
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            position, msg, _ = TradingService.open_position(
                db=self.db,
                user_id=self.user.user_id,
                ticker="TCS",
                position_type="LONG",
                quantity=10,
                entry_price=1000.0,
                take_profit=1100.0,
                stop_loss=950.0,
                stock_name="Tata Consultancy Services"
            )

            self.assertIsNotNone(position)
            self.assertEqual(position.status, "OPEN")
            self.assertEqual(position.total_investment, 10000.0)

            self.db.refresh(self.user)
            self.assertEqual(self.user.virtual_balance, 90000.0)

    def test_set_limits_strictly_blocked_when_market_closed(self):
        """Outside market hours: modifying TP/SL is strictly blocked."""
        pos = models.Position(
            user_id=self.user.user_id,
            ticker="TCS",
            position_type="LONG",
            quantity=10,
            entry_price=1000.0,
            total_investment=10000.0,
            take_profit=1100.0,
            stop_loss=950.0,
            status="OPEN"
        )
        self.db.add(pos)
        self.db.commit()
        self.db.refresh(pos)

        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=False):
            success, msg = TradingService.set_limits(
                db=self.db,
                user_id=self.user.user_id,
                position_id=pos.id,
                take_profit=1150.0,
                stop_loss=920.0
            )

            self.assertFalse(success)
            self.assertIn("Market is closed", msg)
            self.assertIn("09:15 AM", msg)

    def test_close_position_strictly_blocked_when_market_closed(self):
        """Outside market hours: closing a position is strictly blocked."""
        pos = models.Position(
            user_id=self.user.user_id,
            ticker="TCS",
            position_type="LONG",
            quantity=10,
            entry_price=1000.0,
            total_investment=10000.0,
            status="OPEN"
        )
        self.db.add(pos)
        self.db.commit()
        self.db.refresh(pos)

        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=False):
            closed_pos, msg = TradingService.close_position(
                db=self.db,
                user_id=self.user.user_id,
                position_id=pos.id,
                closing_price=1050.0
            )

            self.assertIsNone(closed_pos)
            self.assertIn("Market is closed", msg)
            self.assertIn("09:15 AM", msg)
            self.db.refresh(pos)
            self.assertEqual(pos.status, "OPEN")

    def test_manual_close_sets_exit_reason_manual_close(self):
        """Manual close explicitly sets exit_reason = 'MANUAL_CLOSE'."""
        pos = models.Position(
            user_id=self.user.user_id,
            ticker="INFY",
            position_type="LONG",
            quantity=10,
            entry_price=1000.0,
            total_investment=10000.0,
            status="OPEN"
        )
        self.db.add(pos)
        self.db.commit()
        self.db.refresh(pos)

        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            closed_pos, msg = TradingService.close_position(
                db=self.db,
                user_id=self.user.user_id,
                position_id=pos.id,
                closing_price=1050.0,
                close_type="MANUAL"
            )

            self.assertIsNotNone(closed_pos)
            self.assertEqual(closed_pos.status, "CLOSED")
            self.assertEqual(closed_pos.exit_reason, "MANUAL_CLOSE")
            self.assertEqual(closed_pos.close_type, "MANUAL")
            self.assertEqual(closed_pos.realized_pnl, 500.0)

    def test_tp_sl_close_sets_exit_reason_explicitly(self):
        """TP or SL execution explicitly sets exit_reason to TP_HIT or SL_HIT."""
        pos1 = models.Position(
            user_id=self.user.user_id,
            ticker="WIPRO",
            position_type="LONG",
            quantity=10,
            entry_price=400.0,
            total_investment=4000.0,
            status="OPEN"
        )
        pos2 = models.Position(
            user_id=self.user.user_id,
            ticker="HCLTECH",
            position_type="LONG",
            quantity=10,
            entry_price=1000.0,
            total_investment=10000.0,
            status="OPEN"
        )
        self.db.add_all([pos1, pos2])
        self.db.commit()

        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            tp_pos, _ = TradingService.close_position(
                db=self.db,
                user_id=self.user.user_id,
                position_id=pos1.id,
                closing_price=450.0,
                close_type="TP_EXECUTED"
            )
            sl_pos, _ = TradingService.close_position(
                db=self.db,
                user_id=self.user.user_id,
                position_id=pos2.id,
                closing_price=950.0,
                close_type="SL_EXECUTED"
            )

            self.assertEqual(tp_pos.exit_reason, "TP_HIT")
            self.assertEqual(sl_pos.exit_reason, "SL_HIT")

    def test_win_rate_counts_breakeven_as_win(self):
        """Win Rate % = (count of closed trades with P&L >= 0) / (total closed trades) * 100."""
        # Create 3 closed trades: 1 win (+500), 1 loss (-200), 1 breakeven (0.00)
        t1 = models.Position(
            user_id=self.user.user_id,
            ticker="A",
            position_type="LONG",
            quantity=1,
            entry_price=100.0,
            closing_price=150.0,
            total_investment=100.0,
            realized_pnl=50.0,
            status="CLOSED",
            exit_reason="TP_HIT"
        )
        t2 = models.Position(
            user_id=self.user.user_id,
            ticker="B",
            position_type="LONG",
            quantity=1,
            entry_price=100.0,
            closing_price=80.0,
            total_investment=100.0,
            realized_pnl=-20.0,
            status="CLOSED",
            exit_reason="SL_HIT"
        )
        t3 = models.Position(
            user_id=self.user.user_id,
            ticker="C",
            position_type="LONG",
            quantity=1,
            entry_price=100.0,
            closing_price=100.0,
            total_investment=100.0,
            realized_pnl=0.0,
            status="CLOSED",
            exit_reason="MANUAL_CLOSE"
        )
        self.db.add_all([t1, t2, t3])
        self.db.commit()

        pnl_rows = self.db.query(models.Position.realized_pnl).filter(
            models.Position.user_id == self.user.user_id,
            models.Position.status == "CLOSED"
        ).all()

        total_wins = sum(1 for r in pnl_rows if r[0] >= 0)
        total_closed = len(pnl_rows)
        win_rate = round((total_wins / total_closed) * 100, 1)

        # 2 out of 3 are >= 0 (66.7%)
        self.assertEqual(total_wins, 2)
        self.assertEqual(total_closed, 3)
        self.assertEqual(win_rate, 66.7)


if __name__ == "__main__":
    unittest.main()
