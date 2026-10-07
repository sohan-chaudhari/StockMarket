"""Regression tests for BUG-2: persistent HttpOnly session cookie.

A user who closes and reopens the browser must stay signed in for the token
lifetime, WITHOUT storing the JWT (or any password) in JS-readable storage.

Verified here:
  * login sets an HttpOnly, SameSite=Lax cookie with an explicit Max-Age
  * protected endpoints authenticate from the cookie alone (no Bearer header)
  * an invalid cookie is rejected
  * logout blacklists the token AND clears the cookie (subsequent /me -> 401)
  * blacklisting is idempotent (double logout cannot crash on the unique jti)

Uses the same isolated-app + in-memory-SQLite pattern as tests/test_auth.py, so
it needs no Postgres.
"""
import os
import unittest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-auth-tests-only")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth
import models
from database import Base, get_db
from rate_limiter import limiter
from routers import auth_router


app = FastAPI()
app.state.limiter = limiter
app.include_router(auth_router.router, prefix="/api/auth")


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests"})


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

PASSWORD = "Str0ng!Passw0rd123"
EMAIL = "cookie@example.com"


def _client():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    limiter.reset()
    auth_router._otp_attempts.clear()
    db = _TestingSessionLocal()
    try:
        user = models.User(
            email=EMAIL,
            password_hash=auth.get_password_hash(PASSWORD),
            full_name="Cookie User",
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()
    finally:
        db.close()
    return TestClient(app)


class TestSessionCookie(unittest.TestCase):
    def _login(self, client, email=EMAIL):
        return client.post("/api/auth/login", json={"email": email, "password": PASSWORD})

    def test_login_sets_httponly_samesite_cookie(self):
        client = _client()
        resp = self._login(client)
        self.assertEqual(resp.status_code, 200)
        raw = resp.headers.get("set-cookie", "")
        self.assertIn(auth.AUTH_COOKIE_NAME, raw)
        self.assertIn("HttpOnly", raw)
        self.assertIn("samesite=lax", raw.lower())
        self.assertIn("max-age=", raw.lower())
        # The JSON contract is preserved for the current tab.
        self.assertIn("access_token", resp.json())

    def test_cookie_authenticates_without_bearer_header(self):
        client = _client()
        self._login(client)
        # No Authorization header at all — only the cookie jar.
        resp = client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], EMAIL)

    def test_missing_session_rejected(self):
        client = _client()
        resp = client.get("/api/auth/me")
        self.assertIn(resp.status_code, (401, 403))

    def test_invalid_cookie_rejected(self):
        client = _client()
        client.cookies.set(auth.AUTH_COOKIE_NAME, "not-a-jwt")
        resp = client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_bogus_bearer_falls_back_to_valid_cookie(self):
        # The frontend stores a non-sensitive marker in JS storage; after a
        # browser restart it may send e.g. "Bearer cookie-session" while the
        # real credential is only in the cookie. The cookie must still win.
        client = _client()
        self._login(client)
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer cookie-session"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], EMAIL)

    def test_logout_clears_cookie_and_blacklists(self):
        client = _client()
        self._login(client)
        cookie_value = client.cookies.get(auth.AUTH_COOKIE_NAME)
        self.assertTrue(cookie_value)

        logout = client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)

        # Session no longer usable.
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

        # The exact jti is now blacklisted.
        payload = auth.decode_token(cookie_value)
        db = _TestingSessionLocal()
        try:
            self.assertTrue(auth.is_token_blacklisted(db, payload["jti"]))
        finally:
            db.close()

    def test_logout_is_idempotent(self):
        client = _client()
        self._login(client)
        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(client.post("/api/auth/logout").status_code, 200)

    def test_blacklist_token_idempotent(self):
        _client()  # ensures the SQLite schema exists
        db = _TestingSessionLocal()
        try:
            auth.blacklist_token(db, "jti-idempotent")
            auth.blacklist_token(db, "jti-idempotent")  # must not raise
            self.assertTrue(auth.is_token_blacklisted(db, "jti-idempotent"))
        finally:
            db.close()

    def test_cookie_secure_flag_follows_transport(self):
        # Plain HTTP (dev/test) -> not Secure, or the browser would drop it.
        saved = os.environ.pop("AUTH_COOKIE_SECURE", None)
        try:
            self.assertFalse(auth._cookie_secure(None))
            os.environ["AUTH_COOKIE_SECURE"] = "true"
            self.assertTrue(auth._cookie_secure(None))
        finally:
            os.environ.pop("AUTH_COOKIE_SECURE", None)
            if saved is not None:
                os.environ["AUTH_COOKIE_SECURE"] = saved


if __name__ == "__main__":
    unittest.main()
