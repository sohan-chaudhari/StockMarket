"""SEC-06 regression: authorization for the remaining operational/diagnostic
endpoints, and removal of the unused state-changing route.

Scope:
  * the former `POST /api/yfinance/sync-inactive` was REMOVED -- it had no
    callers and was a verbatim duplicate of the already-scheduled
    `_sync_yfinance_inactive_symbols` job, while being an unauthenticated DB
    write. The scheduled job/helper must remain intact.
  * these seven now require an allowlisted administrator (`auth.require_admin`,
    SEC-05) with their response contracts unchanged:
      /api/candle-health, /api/validation/run, /api/candle-gaps,
      /api/yfinance/report, /api/aggregator-stats, /api/poller-stats,
      /api/monitoring/candle-system

Runs against the REAL `main.app` with an in-memory SQLite DB (dependency
override), with the app's lifespan never started -- so no provider, no network,
no production database and no mail. `/api/validation/run` depends on a service
that is only built during startup, so it is stubbed here.
"""
import inspect
import os
import unittest
from unittest import mock

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-auth-tests-only")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth
import main
import models
from database import Base, get_db


ADMIN_EMAIL = "theleveragecorp@gmail.com"
USER_EMAIL = "ordinary@example.com"

# (path, query params) for the seven admin-gated endpoints.
GATED = [
    ("/api/candle-health", {}),
    ("/api/validation/run", {"ticker": "SEC06X", "timeframe": "5m"}),
    ("/api/candle-gaps", {"ticker": "SEC06X", "timeframe": "5m", "days": 7}),
    ("/api/yfinance/report", {}),
    ("/api/aggregator-stats", {}),
    ("/api/poller-stats", {}),
    ("/api/monitoring/candle-system", {}),
]

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _override_get_db():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


class _AdminEnv:
    """Temporarily set/remove ADMIN_EMAILS (None removes it)."""

    def __init__(self, value):
        self.value = value
        self.saved = None

    def __enter__(self):
        self.saved = os.environ.get("ADMIN_EMAILS")
        if self.value is None:
            os.environ.pop("ADMIN_EMAILS", None)
        else:
            os.environ["ADMIN_EMAILS"] = self.value

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("ADMIN_EMAILS", None)
        else:
            os.environ["ADMIN_EMAILS"] = self.saved


def _setup_db():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    ids = {}
    db = _SessionLocal()
    try:
        for label, email in (("admin", ADMIN_EMAIL), ("user", USER_EMAIL)):
            u = models.User(
                email=email,
                password_hash=auth.get_password_hash("Str0ng!Passw0rd123"),
                full_name=label,
                is_active=True,
                is_verified=True,
            )
            db.add(u)
            db.flush()
            ids[label] = u.user_id
        db.commit()
    finally:
        db.close()
    return ids


def _token(user_id, email):
    return auth.create_access_token({"sub": str(user_id), "user_id": user_id, "email": email})


def _dependency_names(dependant, acc=None):
    """Collect every dependency callable name reachable from a route."""
    if acc is None:
        acc = set()
    for dep in getattr(dependant, "dependencies", []) or []:
        call = getattr(dep, "call", None)
        if call is not None:
            acc.add(getattr(call, "__name__", ""))
        _dependency_names(dep, acc)
    return acc


class OpsEndpointAuthorizationTests(unittest.TestCase):

    def setUp(self):
        self.ids = _setup_db()
        main.app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.pop(get_db, None)

    # ---- unauthenticated ---------------------------------------------------
    def test_unauthenticated_is_denied_on_every_endpoint(self):
        with _AdminEnv(ADMIN_EMAIL):
            for path, params in GATED:
                with self.subTest(path=path):
                    resp = self.client.get(path, params=params)
                    self.assertIn(resp.status_code, (401, 403),
                                  f"{path} must not be reachable anonymously")

    # ---- ordinary authenticated user ---------------------------------------
    def test_ordinary_authenticated_user_is_denied_on_every_endpoint(self):
        headers = {"Authorization": f"Bearer {_token(self.ids['user'], USER_EMAIL)}"}
        with _AdminEnv(ADMIN_EMAIL):
            for path, params in GATED:
                with self.subTest(path=path):
                    resp = self.client.get(path, params=params, headers=headers)
                    self.assertEqual(resp.status_code, 403,
                                     f"{path} must require an administrator")

    # ---- administrator -----------------------------------------------------
    def test_administrator_succeeds_with_unchanged_contracts(self):
        headers = {"Authorization": f"Bearer {_token(self.ids['admin'], ADMIN_EMAIL)}"}
        fake_service = mock.MagicMock()
        fake_service.run_all.return_value = []
        fake_service.generate_report.return_value = {"ok": True, "issues": []}

        with _AdminEnv(ADMIN_EMAIL), \
             mock.patch.object(main, "_validation_service", fake_service):
            for path, params in GATED:
                with self.subTest(path=path):
                    resp = self.client.get(path, params=params, headers=headers)
                    self.assertEqual(resp.status_code, 200, f"{path}: {resp.text}")
                    self.assertIsInstance(resp.json(), dict)

            # Spot-check the response shapes that are stable without startup.
            self.assertIn("total_candles",
                          self.client.get("/api/candle-health", headers=headers).json())
            gaps = self.client.get("/api/candle-gaps", params={"ticker": "SEC06X"},
                                   headers=headers).json()
            self.assertIn("gaps", gaps)
            self.assertIn("gap_count", gaps)
            self.assertIn("status",
                          self.client.get("/api/poller-stats", headers=headers).json())
            self.assertEqual(
                self.client.get("/api/validation/run", params={"ticker": "SEC06X"},
                                headers=headers).json(),
                {"ok": True, "issues": []},
            )

    def test_allowlisted_but_unverified_admin_is_denied(self):
        db = _SessionLocal()
        try:
            u = db.query(models.User).filter(models.User.email == ADMIN_EMAIL).first()
            u.is_verified = False
            db.commit()
        finally:
            db.close()
        headers = {"Authorization": f"Bearer {_token(self.ids['admin'], ADMIN_EMAIL)}"}
        with _AdminEnv(ADMIN_EMAIL):
            resp = self.client.get("/api/yfinance/report", headers=headers)
        self.assertEqual(resp.status_code, 403)

    def test_missing_allowlist_denies_the_admin_too(self):
        headers = {"Authorization": f"Bearer {_token(self.ids['admin'], ADMIN_EMAIL)}"}
        with _AdminEnv(None):
            resp = self.client.get("/api/yfinance/report", headers=headers)
        self.assertEqual(resp.status_code, 403)


class RemovedRouteAndScheduledJobTests(unittest.TestCase):

    def test_sync_inactive_route_is_gone(self):
        paths = {getattr(r, "path", None) for r in main.app.routes}
        self.assertNotIn("/api/yfinance/sync-inactive", paths,
                         "the unauthenticated state-changing route must be removed")
        self.assertFalse(hasattr(main, "yfinance_sync_inactive"),
                         "the route handler function must be removed")

    def test_scheduled_inactive_sync_job_remains_intact(self):
        # helper still exists and is callable
        self.assertTrue(callable(getattr(main, "_sync_yfinance_inactive_symbols", None)),
                        "the scheduled sync helper must be preserved")
        # and is still wired into startup() by the periodic task
        src = inspect.getsource(main.startup)
        self.assertIn("_yfinance_inactive_sync", src)
        self.assertIn("_sync_yfinance_inactive_symbols", src)
        self.assertIn("asyncio.sleep(300)", src)


class PublicBackfillNotAdminGatedTests(unittest.TestCase):
    """SEC-01..SEC-06 must never gate ordinary charting or automatic backfill.

    Admin authorization is for operator/maintenance functions only. A normal
    user opening any ticker must still reach the stock-data/backfill endpoints
    (subject to the existing cooldowns, rate limits and budgets), with no admin
    approval and no routing through the administrator's account.
    """

    PROTECTED_PATHS = {
        # SEC-05
        "/api/holidays/refresh",
        "/api/backfill-symbol/{ticker}",
        "/api/validate-intraday_candles_5min/{ticker}",
        "/api/sessions/refresh",
        # SEC-06
        "/api/candle-health",
        "/api/validation/run",
        "/api/candle-gaps",
        "/api/yfinance/report",
        "/api/aggregator-stats",
        "/api/poller-stats",
        "/api/monitoring/candle-system",
    }

    PUBLIC_PATHS = [
        "/api/stock-data",
        "/api/stock-data/range",
        "/api/stock-data/intraday",
        "/api/stock-data/weekly",
        "/api/stock-data/monthly",
        "/api/stock-data/history-before",
        "/api/stock-data/chart",
        "/api/stock-data/candle/latest",
        "/api/stock-data/intraday/paginated",
        "/api/stock-data/range/paginated",
        "/api/stock-data/intraday/since",
        "/api/live-prices",
        "/api/all-stocks",
        "/api/market-movers",
    ]

    def _routes(self):
        out = {}
        for route in main.app.routes:
            path = getattr(route, "path", None)
            dependant = getattr(route, "dependant", None)
            if path and dependant is not None:
                out[path] = _dependency_names(dependant)
        return out

    def test_all_eleven_protected_paths_require_admin(self):
        routes = self._routes()
        for path in self.PROTECTED_PATHS:
            self.assertIn(path, routes, f"{path} route not registered")
            self.assertIn("require_admin", routes[path], f"{path} must require admin")

    def test_public_chart_and_backfill_paths_are_not_admin_gated(self):
        routes = self._routes()
        for path in self.PUBLIC_PATHS:
            self.assertIn(path, routes, f"{path} route not registered")
            self.assertNotIn(
                "require_admin", routes[path],
                f"{path} must stay reachable for ordinary users (no admin approval)",
            )


if __name__ == "__main__":
    unittest.main()
