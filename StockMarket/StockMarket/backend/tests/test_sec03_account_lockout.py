"""SEC-03 regression: the account lockout must not be a targeted DoS.

Reported finding (audit S3): `login` checked `check_account_locked` BEFORE
verifying the password, so an anonymous visitor who knew only an email address
could burn the 5-failed-attempt budget and deny the real owner for 30 minutes --
repeatable, and requiring no knowledge of the password.

The mitigation verifies the password first:
  * a CORRECT password always signs in and clears the lock, so the owner can
    never be locked out by someone else's failures;
  * a WRONG password is still rejected, and still triggers the lock after 5
    attempts, and does NOT extend an existing lock -- so the 5-attempts /
    30-minutes brute-force ceiling is exactly what it was.

Isolated app + in-memory SQLite (same pattern as tests/test_auth.py). The mail
sender is patched so nothing can leave the machine.
"""
import os
import unittest
from unittest import mock

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
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _override_get_db():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

# Never send real mail: login does not send, but a future edit must not either.
_send_email_patch = None


def setUpModule():
    global _send_email_patch
    _send_email_patch = mock.patch.object(auth, "send_email")
    _send_email_patch.start()


def tearDownModule():
    global _send_email_patch
    if _send_email_patch is not None:
        _send_email_patch.stop()
        _send_email_patch = None


PASSWORD = "Str0ng!Passw0rd123"
WRONG = "WrongPassword123!"
EMAIL = "sec03@example.com"


def _client():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    limiter.reset()
    auth_router._otp_attempts.clear()
    db = _SessionLocal()
    try:
        db.add(models.User(
            email=EMAIL,
            password_hash=auth.get_password_hash(PASSWORD),
            full_name="Sec03 User",
            is_active=True,
            is_verified=True,
        ))
        db.commit()
    finally:
        db.close()
    return TestClient(app)


def _login(client, password):
    return client.post("/api/auth/login", json={"email": EMAIL, "password": password})


def _user_row():
    db = _SessionLocal()
    try:
        return db.query(models.User).filter(models.User.email == EMAIL).first()
    finally:
        db.close()


class AccountLockoutDoSTests(unittest.TestCase):

    def test_five_wrong_passwords_still_lock_the_account(self):
        client = _client()
        for _ in range(5):
            self.assertEqual(_login(client, WRONG).status_code, 401)
        # Brute-force ceiling preserved: a further wrong attempt is refused.
        resp = _login(client, WRONG)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("temporarily locked", resp.json()["detail"])
        self.assertIsNotNone(_user_row().locked_until)

    def test_correct_password_works_while_locked_and_clears_the_lock(self):
        client = _client()
        for _ in range(5):
            _login(client, WRONG)
        self.assertIsNotNone(_user_row().locked_until)

        # The owner is NOT denied by someone else's failed attempts.
        resp = _login(client, PASSWORD)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(client.cookies.get(auth.AUTH_COOKIE_NAME))

        row = _user_row()
        self.assertEqual(row.failed_login_attempts, 0)
        self.assertIsNone(row.locked_until)

    def test_attacker_cannot_extend_an_existing_lock(self):
        client = _client()
        for _ in range(5):
            _login(client, WRONG)
        first_lock = _user_row().locked_until
        for _ in range(5):
            self.assertEqual(_login(client, WRONG).status_code, 403)
        self.assertEqual(
            _user_row().locked_until, first_lock,
            "further failed attempts must not extend the lock window",
        )

    def test_unlocked_wrong_password_is_still_401(self):
        client = _client()
        resp = _login(client, WRONG)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid email or password", resp.json()["detail"])

    def test_inactive_account_with_correct_password_is_rejected(self):
        client = _client()
        db = _SessionLocal()
        try:
            user = db.query(models.User).filter(models.User.email == EMAIL).first()
            user.is_active = False
            db.commit()
        finally:
            db.close()
        resp = _login(client, PASSWORD)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("disabled", resp.json()["detail"])

    def test_unknown_email_does_not_leak_and_is_401(self):
        client = _client()
        resp = client.post("/api/auth/login", json={
            "email": "nobody@example.com", "password": PASSWORD,
        })
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid email or password", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
