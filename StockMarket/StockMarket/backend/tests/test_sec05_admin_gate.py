"""SEC-05 regression: maintenance/operator endpoints require an administrator.

Finding (audit S5): `/api/holidays/refresh`, `/api/backfill-symbol/{ticker}`,
`/api/validate-intraday_candles_5min/{ticker}` and `/api/sessions/refresh` only
required *any* authenticated account, so an ordinary user could drive NSE
scraping, yfinance backfills and candle/holiday writes.

There is no role column, so "admin" is configuration: a comma-separated email
allowlist in `ADMIN_EMAILS`. `auth.require_admin` requires an authenticated,
ACTIVE (get_current_user), VERIFIED account whose email is allowlisted, and
FAILS CLOSED when the allowlist is unset or empty.

Isolated app + in-memory SQLite. No provider calls, no mail, no production.
"""
import os
import unittest
from unittest import mock  # noqa: F401  (kept for symmetry with sibling suites)

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-auth-tests-only")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth
import models
from database import Base, get_db


ADMIN_EMAIL = "theleveragecorp@gmail.com"   # the intended administrator account
USER_EMAIL = "ordinary@example.com"


app = FastAPI()


@app.get("/admin-probe")
def _admin_probe(current_user: models.User = Depends(auth.require_admin)):
    return {"email": current_user.email}


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


app.dependency_overrides[get_db] = _override_get_db


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


def _make_user(email, is_verified=True, is_active=True):
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    db = _SessionLocal()
    try:
        user = models.User(
            email=email,
            password_hash=auth.get_password_hash("Str0ng!Passw0rd123"),
            full_name="Sec05 User",
            is_active=is_active,
            is_verified=is_verified,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.user_id
    finally:
        db.close()


def _token(user_id, email):
    return auth.create_access_token({"sub": str(user_id), "user_id": user_id, "email": email})


def _get(user_id, email):
    return TestClient(app).get(
        "/admin-probe", headers={"Authorization": f"Bearer {_token(user_id, email)}"}
    )


class AdminGateTests(unittest.TestCase):

    def test_allowlisted_verified_active_user_is_allowed(self):
        uid = _make_user(ADMIN_EMAIL)
        with _AdminEnv(ADMIN_EMAIL):
            resp = _get(uid, ADMIN_EMAIL)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["email"], ADMIN_EMAIL)

    def test_unauthenticated_request_is_rejected(self):
        _make_user(ADMIN_EMAIL)
        with _AdminEnv(ADMIN_EMAIL):
            resp = TestClient(app).get("/admin-probe")
        self.assertIn(resp.status_code, (401, 403))

    def test_allowlisted_but_unverified_user_is_rejected(self):
        uid = _make_user(ADMIN_EMAIL, is_verified=False)
        with _AdminEnv(ADMIN_EMAIL):
            resp = _get(uid, ADMIN_EMAIL)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("verified", resp.json()["detail"].lower())

    def test_allowlisted_but_inactive_user_is_rejected(self):
        uid = _make_user(ADMIN_EMAIL, is_active=False)
        with _AdminEnv(ADMIN_EMAIL):
            resp = _get(uid, ADMIN_EMAIL)
        self.assertEqual(resp.status_code, 403)

    def test_authenticated_but_not_allowlisted_user_is_rejected(self):
        uid = _make_user(USER_EMAIL)
        with _AdminEnv(ADMIN_EMAIL):
            resp = _get(uid, USER_EMAIL)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("administrator", resp.json()["detail"].lower())

    def test_missing_allowlist_fails_closed(self):
        uid = _make_user(ADMIN_EMAIL)
        with _AdminEnv(None):            # ADMIN_EMAILS not set at all
            resp = _get(uid, ADMIN_EMAIL)
        self.assertEqual(resp.status_code, 403,
                         "an unset ADMIN_EMAILS must deny everyone, not open the gate")

    def test_empty_allowlist_fails_closed(self):
        uid = _make_user(ADMIN_EMAIL)
        with _AdminEnv("   "):
            resp = _get(uid, ADMIN_EMAIL)
        self.assertEqual(resp.status_code, 403)

    def test_allowlist_is_case_insensitive_and_trims_spaces(self):
        uid = _make_user(ADMIN_EMAIL)
        with _AdminEnv(" Other@Example.com , " + ADMIN_EMAIL.upper() + " "):
            resp = _get(uid, ADMIN_EMAIL)
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_admin_allowlist_parsing(self):
        with _AdminEnv("a@b.com, C@D.com ,, "):
            self.assertEqual(auth.admin_allowlist(), {"a@b.com", "c@d.com"})
        with _AdminEnv(None):
            self.assertEqual(auth.admin_allowlist(), set())


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


class MaintenanceEndpointWiringTests(unittest.TestCase):
    """The four real endpoints must be wired to require_admin."""

    def test_four_maintenance_endpoints_depend_on_require_admin(self):
        import main  # heavy import, done lazily

        wanted = {
            "/api/holidays/refresh",
            "/api/backfill-symbol/{ticker}",
            "/api/validate-intraday_candles_5min/{ticker}",
            "/api/sessions/refresh",
        }
        found = {}
        for route in main.app.routes:
            path = getattr(route, "path", None)
            dependant = getattr(route, "dependant", None)
            if path in wanted and dependant is not None:
                found[path] = _dependency_names(dependant)

        for path in wanted:
            self.assertIn(path, found, f"{path} route not found in main.app")
            self.assertIn("require_admin", found[path],
                          f"{path} must depend on auth.require_admin")


if __name__ == "__main__":
    unittest.main()
