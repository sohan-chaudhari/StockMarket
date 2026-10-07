"""Regression tests for the P0.1 "no sync DB work on the event loop" fix.

Guards:
  1. routes that were entirely synchronous DB work are now sync `def` (FastAPI
     runs them in its worker threadpool instead of the event loop);
  2. routes that must stay `async` offload their blocking DB work via
     asyncio.to_thread (proved by inspecting the function source);
  3. the affected routes still return the same response shapes and honour auth;
  4. per-request DB sessions are created and closed in balance (no leaks).
"""
import inspect
import os
import threading
import unittest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-audit-tests-only-0123456789")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import models
import auth
from database import Base, get_db, get_ist_now


# Routes converted async def -> def (must NOT be coroutine functions).
CONVERTED_SYNC = [
    "place_order",
    "get_closed_positions",
    "get_transactions",
    "get_watchlist",
    "proxy_news_ticker",
]

# Routes that must stay async AND offload their blocking work with to_thread.
STAY_ASYNC_OFFLOAD = [
    "refresh_movers_snapshot",
    "news_search",
    "proxy_news_general",
    "proxy_market_sentiment",
    "get_fii_dii",
    "get_screener",
    "user_websocket_endpoint",
]


class TestRouteClassification(unittest.TestCase):
    def test_converted_routes_are_sync_not_coroutines(self):
        for name in CONVERTED_SYNC:
            fn = getattr(main, name)
            self.assertFalse(
                inspect.iscoroutinefunction(fn),
                f"{name} must be a sync `def` so FastAPI runs it in the threadpool, not on the event loop",
            )

    def test_stay_async_routes_offload_blocking_work(self):
        for name in STAY_ASYNC_OFFLOAD:
            fn = getattr(main, name)
            self.assertTrue(inspect.iscoroutinefunction(fn), f"{name} must remain async")
            src = inspect.getsource(fn)
            self.assertIn(
                "asyncio.to_thread", src,
                f"{name} keeps genuine async I/O, so its synchronous DB work must be offloaded via asyncio.to_thread",
            )

    def test_refresh_movers_snapshot_split(self):
        # sync body + async offloading wrapper; the async loop awaits the wrapper.
        self.assertFalse(inspect.iscoroutinefunction(main._refresh_movers_snapshot_sync))
        self.assertTrue(inspect.iscoroutinefunction(main.refresh_movers_snapshot))
        self.assertIn("asyncio.to_thread", inspect.getsource(main.refresh_movers_snapshot))


# ── Functional tests on an isolated in-memory SQLite DB ──────────────────────
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

_created = []
_closed = []


def _override_get_db():
    db = _TestingSession()
    _created.append(1)
    try:
        yield db
    finally:
        db.close()
        _closed.append(1)


class TestConvertedRoutesFunctional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.dependency_overrides[get_db] = _override_get_db
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.pop(get_db, None)

    def setUp(self):
        Base.metadata.drop_all(_engine)
        Base.metadata.create_all(_engine)
        _created.clear()
        _closed.clear()
        db = _TestingSession()
        try:
            u = models.User(
                email="audit@example.com",
                password_hash=auth.get_password_hash("Str0ng!Passw0rd123"),
                full_name="Audit User",
                is_active=True,
                is_verified=True,
            )
            db.add(u)
            db.commit()
            db.refresh(u)
            self.user_id = u.user_id
            db.add(models.UserWatchlist(user_id=u.user_id, ticker="SBIN"))
            db.add(models.Position(
                user_id=u.user_id, ticker="SBIN", position_type="LONG", quantity=1,
                entry_price=100.0, total_investment=100.0, status="CLOSED",
                closing_price=110.0, realized_pnl=10.0, close_type="MANUAL",
                closed_at=get_ist_now(),
            ))
            db.add(models.Transaction(
                user_id=u.user_id, transaction_type="OPEN", amount=-100.0,
                balance_after=100000.0, ticker="SBIN",
            ))
            db.commit()
        finally:
            db.close()
        self.token = auth.create_access_token(
            data={"sub": str(self.user_id), "user_id": self.user_id, "email": "audit@example.com"}
        )
        self.h = {"Authorization": f"Bearer {self.token}"}

    def test_watchlist_unauthenticated_shape(self):
        r = self.client.get("/api/watchlist")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("watchlist", body)
        self.assertIn("count", body)

    def test_watchlist_authenticated_shape(self):
        r = self.client.get("/api/watchlist", headers=self.h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["watchlist"][0]["ticker"], "SBIN")

    def test_transactions_shape_and_auth(self):
        r = self.client.get("/api/portfolio/transactions", headers=self.h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ("transactions", "total", "page", "pages"):
            self.assertIn(key, body)
        self.assertGreaterEqual(body["total"], 1)
        self.assertEqual(self.client.get("/api/portfolio/transactions").status_code, 401)

    def test_closed_positions_shape_and_auth(self):
        r = self.client.get("/api/portfolio/closed-positions", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)
        self.assertEqual(r.json()[0]["ticker"], "SBIN")
        self.assertEqual(self.client.get("/api/portfolio/closed-positions").status_code, 401)

    def test_news_ticker_public_shape(self):
        r = self.client.get("/api/news/ticker/SBIN")
        self.assertEqual(r.status_code, 200)
        self.assertIn("articles", r.json())

    def test_place_order_requires_csrf_and_auth(self):
        # CSRF dependency is evaluated before the handler -> blocked without the token.
        r = self.client.post(
            "/api/trade/place-order",
            json={"ticker": "SBIN", "position_type": "LONG", "quantity": 1, "entry_price": 100.0},
            headers=self.h,
        )
        self.assertIn(r.status_code, (401, 403))

    def test_db_sessions_created_and_closed_in_balance(self):
        for _ in range(5):
            self.client.get("/api/watchlist", headers=self.h)
            self.client.get("/api/portfolio/transactions", headers=self.h)
        self.assertGreater(len(_created), 0)
        self.assertEqual(len(_created), len(_closed), "every per-request DB session must be closed")


class TestThreadpoolExecution(unittest.TestCase):
    """Runtime proof of the mechanism: a *sync* route is executed by FastAPI in
    its worker threadpool (a different thread from the event loop), while an
    *async* route runs on the event-loop thread. Therefore the routes converted
    to `def` (verified above) no longer execute their synchronous DB work on the
    event loop."""

    def test_sync_route_does_not_run_on_event_loop_thread(self):
        probe = FastAPI()
        observed = {}

        @probe.get("/__loop_ident")
        async def _loop_ident():
            # async endpoint -> runs on the event-loop thread
            observed["loop"] = threading.get_ident()
            return {"ident": observed["loop"]}

        @probe.get("/__worker_ident")
        def _worker_ident():
            # sync endpoint -> FastAPI dispatches to its threadpool
            return {"ident": threading.get_ident()}

        client = TestClient(probe)
        client.get("/__loop_ident")
        worker_ident = client.get("/__worker_ident").json()["ident"]

        self.assertIn("loop", observed)
        self.assertNotEqual(
            worker_ident, observed["loop"],
            "a sync route must run in a worker thread, never on the event-loop thread",
        )


if __name__ == "__main__":
    unittest.main()
