"""SEC-04 regression: an unverified email must not be able to sign in.

Finding (audit S4): `register` set `is_verified=False` and the verification flow
existed, but `login` never checked it -- an unverified account could sign in (and
therefore trade) exactly like a verified one.

The enforcement is deliberately narrow:
  * only an EXPLICIT `is_verified is False` blocks. `is_verified` is NULLABLE in
    the database (legacy rows created before the registration flow), and blocking
    a NULL would lock existing accounts out -- so NULL keeps working;
  * the check runs AFTER the password and `is_active` checks, so the verification
    state is only revealed to a caller who already proved the password, and a
    denied attempt has no side effects;
  * the verification flow itself is untouched and still works logged-out
    (`/api/auth/verify-email`, `/api/auth/resend-verification`).

Isolated app + in-memory SQLite (same pattern as test_auth.py). The mail sender
is patched for the whole module so nothing can leave the machine.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-auth-tests-only")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, text
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

# Registration/verification go through auth.send_email(); never send for real.
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


def _reset():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    limiter.reset()
    auth_router._otp_attempts.clear()


def _add_user(email, is_verified=True, is_active=True):
    db = _SessionLocal()
    try:
        db.add(models.User(
            email=email,
            password_hash=auth.get_password_hash(PASSWORD),
            full_name="Sec04 User",
            is_active=is_active,
            is_verified=is_verified,
        ))
        db.commit()
    finally:
        db.close()


def _login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _row(email):
    db = _SessionLocal()
    try:
        return db.query(models.User).filter(models.User.email == email).first()
    finally:
        db.close()


class EmailVerificationLoginTests(unittest.TestCase):

    def test_verified_account_can_sign_in(self):
        _reset()
        _add_user("ok@example.com", is_verified=True)
        client = TestClient(app)
        resp = _login(client, "ok@example.com")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(client.cookies.get(auth.AUTH_COOKIE_NAME))
        self.assertIsNotNone(_row("ok@example.com").last_login)

    def test_unverified_account_cannot_sign_in(self):
        _reset()
        _add_user("nv@example.com", is_verified=False)
        client = TestClient(app)
        resp = _login(client, "nv@example.com")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("verify your email", resp.json()["detail"].lower())
        # No session may be issued.
        self.assertFalse(client.cookies.get(auth.AUTH_COOKIE_NAME))
        self.assertIsNone(resp.json().get("access_token"))

    def test_unverified_with_wrong_password_stays_generic(self):
        # The verification state must not leak to someone who does not have the
        # password.
        _reset()
        _add_user("nv2@example.com", is_verified=False)
        resp = _login(TestClient(app), "nv2@example.com", WRONG)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid email or password", resp.json()["detail"])

    def test_inactive_verified_account_is_rejected(self):
        _reset()
        _add_user("inact@example.com", is_verified=True, is_active=False)
        resp = _login(TestClient(app), "inact@example.com")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("disabled", resp.json()["detail"])

    def test_inactive_unverified_prefers_account_disabled(self):
        _reset()
        _add_user("inactnv@example.com", is_verified=False, is_active=False)
        resp = _login(TestClient(app), "inactnv@example.com")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("disabled", resp.json()["detail"])

    def test_unknown_email_is_generic_401(self):
        _reset()
        resp = _login(TestClient(app), "nobody@example.com")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid email or password", resp.json()["detail"])

    def test_legacy_null_is_verified_is_guided_to_verification_not_500(self):
        # Existing-account compatibility: `is_verified` is nullable. A NULL row
        # used to reach the response model as NULL and fail validation with a 500.
        # It must now get the same actionable verification error as an unverified
        # account -- never a crash -- and the verification flow can still fix it.
        _reset()
        _add_user("legacy@example.com", is_verified=True)
        db = _SessionLocal()
        try:
            db.execute(text("UPDATE users SET is_verified = NULL WHERE email = 'legacy@example.com'"))
            db.commit()
        finally:
            db.close()
        self.assertIsNone(_row("legacy@example.com").is_verified)

        resp = _login(TestClient(app), "legacy@example.com")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("verify your email", resp.json()["detail"].lower())

        # ...and once verified through the normal flow, the account signs in.
        db = _SessionLocal()
        try:
            row = db.query(models.User).filter(models.User.email == "legacy@example.com").first()
            row.is_verified = True
            db.commit()
        finally:
            db.close()
        self.assertEqual(_login(TestClient(app), "legacy@example.com").status_code, 200)

    def test_denied_unverified_login_has_no_side_effects(self):
        _reset()
        _add_user("sidfx@example.com", is_verified=False)
        self.assertEqual(_login(TestClient(app), "sidfx@example.com").status_code, 403)
        row = _row("sidfx@example.com")
        self.assertIsNone(row.last_login, "a denied login must not record a last_login")
        self.assertEqual(row.failed_login_attempts, 0)
        self.assertIsNone(row.locked_until)

    def test_verification_flow_then_sign_in(self):
        # End-to-end: register -> blocked login -> the EXISTING verification flow
        # (OTP, logged out) -> sign in succeeds.
        _reset()
        client = TestClient(app)
        reg = client.post("/api/auth/register", json={
            "email": "flow@example.com", "password": PASSWORD, "full_name": "Flow User",
        })
        self.assertEqual(reg.status_code, 201, reg.text)

        blocked = _login(client, "flow@example.com")
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("verify your email", blocked.json()["detail"].lower())

        db = _SessionLocal()
        try:
            tok = db.query(models.VerificationToken).filter(
                models.VerificationToken.token_type == "email_verification"
            ).first()
            otp = tok.otp_code
        finally:
            db.close()

        verified = client.post("/api/auth/verify-email", json={"otp": otp})
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(_row("flow@example.com").is_verified)

        ok = _login(client, "flow@example.com")
        self.assertEqual(ok.status_code, 200, ok.text)


if __name__ == "__main__":
    unittest.main()
