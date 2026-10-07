"""Regression tests for server-side order idempotency (Issue-1 hardening).

A client-generated `client_order_id` identifies ONE submission attempt.
Replaying the same key (double-click, browser/network retry, duplicate frontend
handler) must return the ORIGINAL position instead of opening a second one,
while genuinely separate orders (different keys, including repeat entries in the
same stock) must keep working, and clients that send no key must be unaffected.

The atomicity arbitrator is the DB UNIQUE(user_id, client_order_id); the
concurrent test below exercises it directly from two threads.
"""
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import models
import schemas
from database import Base
from trade_service import TradingService


def _mk_user_engine_memory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


class IdempotencyServiceTests(unittest.TestCase):
    """Service-level: the UNIQUE(user_id, client_order_id) constraint is the
    arbiter for a replayed key."""

    def setUp(self):
        self.engine, self.Session = _mk_user_engine_memory()
        self.db = self.Session()
        self.user = models.User(email="idem@example.com", password_hash="x", virtual_balance=100000.0)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def _open(self, db, key):
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
            return TradingService.open_position(
                db=db, user_id=self.user.user_id, ticker="TCS", position_type="LONG",
                quantity=1, entry_price=100.0, take_profit=110.0, stop_loss=90.0,
                stock_name="TCS", client_order_id=key,
            )

    def _balance(self):
        self.db.expire_all()
        return self.db.query(models.User).filter_by(user_id=self.user.user_id).first().virtual_balance

    def test_replayed_key_is_rejected_by_unique_constraint(self):
        pos1, msg1, _ = self._open(self.db, "K-1")
        self.assertIsNotNone(pos1)
        bal_after_first = self._balance()
        pos2, msg2, _ = self._open(self.db, "K-1")  # same key
        self.assertIsNone(pos2, "replayed key must not open a second position")
        self.assertEqual(
            self.db.query(models.Position).count(), 1,
            "exactly one position must exist for a replayed key",
        )
        self.assertEqual(bal_after_first, self._balance(),
                         "replayed key must not debit the balance again")

    def test_different_keys_open_separate_positions(self):
        self._open(self.db, "K-2")
        self._open(self.db, "K-3")
        self.assertEqual(self.db.query(models.Position).count(), 2)

    def test_repeat_entry_same_stock_is_allowed(self):
        # Two intentional entries in the SAME stock under different keys.
        self._open(self.db, "K-4")
        pos_b, _, _ = self._open(self.db, "K-5")
        self.assertIsNotNone(pos_b)
        self.assertEqual(
            self.db.query(models.Position).filter_by(ticker="TCS").count(), 2,
            "legitimate repeat entries in the same stock must still work",
        )

    def test_null_keys_are_independent(self):
        # Backward compatibility: a client that sends no key behaves as before.
        self._open(self.db, None)
        self._open(self.db, None)
        self.assertEqual(self.db.query(models.Position).count(), 2)


class IdempotencyConcurrencyTests(unittest.TestCase):
    """Two near-simultaneous identical requests must not both create positions.

    Uses a file-backed SQLite database (one connection per thread) and a barrier
    so both threads issue their INSERT at the same moment. The DB UNIQUE
    constraint guarantees exactly one commits; the other either gets
    IntegrityError or a lock timeout (both roll back -> still one position)."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(
            "sqlite:///" + self.path.replace("\\", "/"),
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        s = self.Session()
        self.user = models.User(email="conc@example.com", password_hash="x", virtual_balance=100000.0)
        s.add(self.user)
        s.commit()
        self.user_id = self.user.user_id
        s.close()

    def tearDown(self):
        self.engine.dispose()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_concurrent_identical_requests_create_one_position(self):
        barrier = threading.Barrier(2)
        results = []

        def worker():
            db = self.Session()
            try:
                barrier.wait(timeout=10)
                with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True):
                    pos, msg, _ = TradingService.open_position(
                        db=db, user_id=self.user_id, ticker="TCS", position_type="LONG",
                        quantity=1, entry_price=100.0, take_profit=110.0, stop_loss=90.0,
                        stock_name="TCS", client_order_id="K-CONCURRENT",
                    )
                results.append(pos is not None)
            except Exception as e:  # SQLite lock contention is an acceptable loser path
                results.append(False)
                results.append("EXC:%s" % type(e).__name__)
            finally:
                try:
                    db.rollback()
                except Exception:
                    pass
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: worker(), range(2)))

        check = self.Session()
        try:
            n = check.execute(select(models.Position)).scalars().all()
            self.assertEqual(len(n), 1,
                             "two concurrent identical requests must create exactly one position")
        finally:
            check.close()


class IdempotencyEndpointTests(unittest.TestCase):
    """Endpoint-level: the real /api/trade/place-order handler must return the
    original position for a replayed client_order_id."""

    @classmethod
    def setUpClass(cls):
        import main  # heavy import, shared across the class
        cls.main = main

    def setUp(self):
        self.engine, self.Session = _mk_user_engine_memory()
        self.db = self.Session()
        self.user = models.User(email="ep@example.com", password_hash="x", virtual_balance=100000.0)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def _order(self, key):
        return schemas.PlaceOrderRequest(
            ticker="TCS", position_type="LONG", quantity=1, entry_price=100.0,
            take_profit=110.0, stop_loss=90.0, client_order_id=key,
        )

    def _place(self, key):
        with patch("exchange_calendar.nse_calendar.is_market_open", return_value=True), \
             patch("execution_engine.price_monitor.get_price", return_value=None):
            return self.main.place_order(order=self._order(key), current_user=self.user, db=self.db)

    def test_sequential_duplicate_returns_original_position(self):
        r1 = self._place("EP-1")
        self.assertIsNotNone(r1["position_id"])
        self.assertFalse(r1["is_duplicate"])
        bal1 = r1["balance"]

        r2 = self._place("EP-1")  # replayed request
        self.assertEqual(r2["position_id"], r1["position_id"],
                         "replayed request must return the ORIGINAL position id")
        self.assertTrue(r2["is_duplicate"])
        self.assertEqual(r2["balance"], bal1, "balance must not change on a replay")
        self.assertEqual(self.db.query(models.Position).count(), 1)

    def test_different_keys_create_two_positions(self):
        r1 = self._place("EP-2")
        r2 = self._place("EP-3")
        self.assertNotEqual(r1["position_id"], r2["position_id"])
        self.assertEqual(self.db.query(models.Position).count(), 2)

    def test_no_key_is_backward_compatible(self):
        r1 = self._place(None)
        r2 = self._place(None)
        self.assertNotEqual(r1["position_id"], r2["position_id"])
        self.assertEqual(self.db.query(models.Position).count(), 2)


if __name__ == "__main__":
    unittest.main()
