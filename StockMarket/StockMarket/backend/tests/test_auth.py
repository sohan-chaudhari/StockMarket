"""Tests for backend/auth.py and backend/routers/auth_router.py.

Written for the pre-AWS 1GB production-hardening audit. auth.py/auth_router.py
had ZERO test coverage despite being the most security-sensitive code in the
app: password hashing, JWT issuance/blacklisting, account lockout, email
verification OTP, and password reset.

Uses an isolated FastAPI app (not the real main.py -- avoids paying the cost
of Angel One login / DB migrations / ~22 background tasks) mounting only the
auth router, backed by an in-memory SQLite DB shared across connections via
StaticPool. Mirrors two existing patterns in this suite:
  - test_rate_limiting.py: isolated app reusing the real shared `limiter`
    singleton so results are representative of production wiring.
  - test_amo_queue_system.py: SQLite + Base.metadata.create_all for
    model-backed tests without a live Postgres.
"""
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

# auth.py raises RuntimeError at import time if this is unset. If the real
# backend/.env is present (normal dev setup), auth.py's own load_dotenv()
# already provides a real value and this setdefault is a no-op fallback for
# CI-like environments without a .env file.
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
from database import Base, get_db, get_ist_now
from rate_limiter import limiter
from routers import auth_router


# ---------------------------------------------------------------------------
# Isolated app + in-memory DB wiring (module scope, built once like
# test_rate_limiting.py does -- re-decorating routes per-test would silently
# duplicate slowapi's per-route Limit entries).
# ---------------------------------------------------------------------------
app = FastAPI()
app.state.limiter = limiter
app.include_router(auth_router.router, prefix="/api/auth")


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


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

STRONG_PASSWORD = "Str0ng!Passw0rd123"


def _fresh_client():
    """Reset schema + rate-limit + OTP-throttle state; return a new client.

    Mirrors test_rate_limiting.py's setUp/tearDown reset of `limiter`, plus
    clears auth_router's own module-level `_otp_attempts` dict (an
    in-memory-per-IP limiter separate from slowapi) so tests don't leak
    state into each other via TestClient's fixed "testclient" client host.
    """
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    limiter.reset()
    auth_router._otp_attempts.clear()
    return TestClient(app)


def _db_session():
    return _TestingSessionLocal()


def _make_user(db, email="trader@example.com", password=STRONG_PASSWORD, is_active=True, is_verified=True):
    user = models.User(
        email=email,
        password_hash=auth.get_password_hash(password),
        full_name="Test Trader",
        is_active=is_active,
        is_verified=is_verified,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Pure-function unit tests (no DB, no HTTP)
# ---------------------------------------------------------------------------

class TestPasswordValidation(unittest.TestCase):
    def test_valid_password_accepted(self):
        ok, msg = auth.validate_password(STRONG_PASSWORD)
        self.assertTrue(ok)
        self.assertEqual(msg, "Password is valid")

    def test_too_short_rejected(self):
        ok, msg = auth.validate_password("Sh0rt!Aa")
        self.assertFalse(ok)
        self.assertIn("at least 12 characters", msg)

    def test_missing_uppercase_rejected(self):
        ok, msg = auth.validate_password("lowercase123!aaaa")
        self.assertFalse(ok)
        self.assertIn("uppercase", msg)

    def test_missing_lowercase_rejected(self):
        ok, msg = auth.validate_password("UPPERCASE123!AAAA")
        self.assertFalse(ok)
        self.assertIn("lowercase", msg)

    def test_missing_digit_rejected(self):
        ok, msg = auth.validate_password("NoDigitsHere!AAA")
        self.assertFalse(ok)
        self.assertIn("number", msg)

    def test_missing_special_char_rejected(self):
        ok, msg = auth.validate_password("NoSpecialChar1234")
        self.assertFalse(ok)
        self.assertIn("special character", msg)


class TestPasswordHashing(unittest.TestCase):
    def test_hash_is_not_plaintext(self):
        hashed = auth.get_password_hash(STRONG_PASSWORD)
        self.assertNotEqual(hashed, STRONG_PASSWORD)
        self.assertTrue(hashed.startswith("$2b$") or hashed.startswith("$2a$"))

    def test_correct_password_verifies(self):
        hashed = auth.get_password_hash(STRONG_PASSWORD)
        self.assertTrue(auth.verify_password(STRONG_PASSWORD, hashed))

    def test_wrong_password_fails(self):
        hashed = auth.get_password_hash(STRONG_PASSWORD)
        self.assertFalse(auth.verify_password("WrongPassword123!", hashed))

    def test_two_hashes_of_same_password_differ(self):
        # bcrypt salts each hash independently -- guards against a
        # regression to a non-salted or deterministic scheme.
        h1 = auth.get_password_hash(STRONG_PASSWORD)
        h2 = auth.get_password_hash(STRONG_PASSWORD)
        self.assertNotEqual(h1, h2)
        self.assertTrue(auth.verify_password(STRONG_PASSWORD, h1))
        self.assertTrue(auth.verify_password(STRONG_PASSWORD, h2))

    def test_verify_password_malformed_hash_returns_false_not_raise(self):
        # verify_password wraps bcrypt in try/except -- a corrupt/legacy
        # hash must fail closed, never raise past the auth layer.
        self.assertFalse(auth.verify_password(STRONG_PASSWORD, "not-a-real-bcrypt-hash"))


class TestJWT(unittest.TestCase):
    def test_round_trip(self):
        token = auth.create_access_token(data={"sub": "1", "user_id": 1, "email": "a@b.com"})
        payload = auth.decode_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["user_id"], 1)
        self.assertEqual(payload["email"], "a@b.com")

    def test_token_has_unique_jti_for_blacklisting(self):
        t1 = auth.create_access_token(data={"user_id": 1})
        t2 = auth.create_access_token(data={"user_id": 1})
        p1, p2 = auth.decode_token(t1), auth.decode_token(t2)
        self.assertIn("jti", p1)
        self.assertNotEqual(p1["jti"], p2["jti"])

    def test_expired_token_rejected(self):
        token = auth.create_access_token(data={"user_id": 1}, expires_delta=timedelta(seconds=-1))
        self.assertIsNone(auth.decode_token(token))

    def test_tampered_token_rejected(self):
        token = auth.create_access_token(data={"user_id": 1})
        tampered = token[:-4] + ("A" * 4 if not token.endswith("AAAA") else "BBBB")
        self.assertIsNone(auth.decode_token(tampered))

    def test_garbage_token_rejected(self):
        self.assertIsNone(auth.decode_token("this.is.not.a.jwt"))


class TestAccountLockout(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(_engine)
        Base.metadata.create_all(_engine)
        self.db = _db_session()
        self.user = _make_user(self.db)

    def tearDown(self):
        self.db.close()

    def test_not_locked_initially(self):
        locked, msg = auth.check_account_locked(self.user)
        self.assertFalse(locked)
        self.assertIsNone(msg)

    def test_locks_after_five_failed_attempts(self):
        remaining = None
        for _ in range(5):
            remaining = auth.handle_failed_login(self.db, self.user)
        self.assertEqual(remaining, 0)
        locked, msg = auth.check_account_locked(self.user)
        self.assertTrue(locked)
        self.assertIn("temporarily locked", msg)

    def test_remaining_attempts_counts_down(self):
        remaining = auth.handle_failed_login(self.db, self.user)
        self.assertEqual(remaining, 4)
        remaining = auth.handle_failed_login(self.db, self.user)
        self.assertEqual(remaining, 3)

    def test_reset_clears_lockout_and_counter(self):
        for _ in range(5):
            auth.handle_failed_login(self.db, self.user)
        auth.reset_failed_attempts(self.db, self.user)
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)
        locked, _ = auth.check_account_locked(self.user)
        self.assertFalse(locked)

    def test_reset_sets_last_login(self):
        self.assertIsNone(self.user.last_login)
        auth.reset_failed_attempts(self.db, self.user)
        self.assertIsNotNone(self.user.last_login)


class TestTokenBlacklist(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(_engine)
        Base.metadata.create_all(_engine)
        self.db = _db_session()

    def tearDown(self):
        self.db.close()

    def test_unknown_jti_not_blacklisted(self):
        self.assertFalse(auth.is_token_blacklisted(self.db, "never-issued-jti"))

    def test_blacklisted_jti_detected(self):
        auth.blacklist_token(self.db, "some-jti-value")
        self.assertTrue(auth.is_token_blacklisted(self.db, "some-jti-value"))


# ---------------------------------------------------------------------------
# HTTP-level integration tests against the real auth_router
# ---------------------------------------------------------------------------

class TestRegisterEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = _fresh_client()
        self._send_email_patch = patch.object(auth, "send_email")
        self._send_email_patch.start()

    def tearDown(self):
        self._send_email_patch.stop()

    def test_register_success(self):
        resp = self.client.post("/api/auth/register", json={
            "email": "new@example.com", "password": STRONG_PASSWORD, "full_name": "New User",
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertIn("user_id", body)

        db = _db_session()
        try:
            user = db.query(models.User).filter(models.User.email == "new@example.com").first()
            self.assertIsNotNone(user)
            self.assertFalse(user.is_verified)
            self.assertNotEqual(user.password_hash, STRONG_PASSWORD)
            token = db.query(models.VerificationToken).filter(
                models.VerificationToken.user_id == user.user_id
            ).first()
            self.assertIsNotNone(token)
            self.assertEqual(token.token_type, "email_verification")
            self.assertIsNotNone(token.otp_code)
        finally:
            db.close()

    def test_register_duplicate_email_rejected(self):
        db = _db_session()
        _make_user(db, email="dup@example.com")
        db.close()

        resp = self.client.post("/api/auth/register", json={
            "email": "dup@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already registered", resp.json()["detail"])

    def test_register_weak_password_rejected(self):
        resp = self.client.post("/api/auth/register", json={
            "email": "weak@example.com", "password": "short1!",
        })
        self.assertEqual(resp.status_code, 400)

        db = _db_session()
        try:
            self.assertIsNone(
                db.query(models.User).filter(models.User.email == "weak@example.com").first()
            )
        finally:
            db.close()

    def test_register_rate_limited_after_ten_per_hour(self):
        for i in range(10):
            resp = self.client.post("/api/auth/register", json={
                "email": f"user{i}@example.com", "password": STRONG_PASSWORD,
            })
            self.assertEqual(resp.status_code, 201)
        resp = self.client.post("/api/auth/register", json={
            "email": "one_too_many@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(resp.status_code, 429)


class TestRegisterAtomicity(unittest.TestCase):
    """HARDEN-03 regression: register() used to commit the User row, then
    separately commit the VerificationToken row -- if the second commit
    failed, a User row was left permanently persisted with no verification
    token. Now both inserts share one transaction (db.flush() to obtain
    new_user.user_id, then a single db.commit() after both db.add() calls),
    so a failure between them must leave NEITHER row persisted."""

    def setUp(self):
        self.client = _fresh_client()
        self._send_email_patch = patch.object(auth, "send_email")
        self._send_email_patch.start()

    def tearDown(self):
        self._send_email_patch.stop()

    def test_normal_registration_persists_both_rows_in_one_request(self):
        """The successful path, exercised again here specifically to confirm
        the atomicity refactor (flush + single commit) didn't change it."""
        resp = self.client.post("/api/auth/register", json={
            "email": "atomic_ok@example.com", "password": STRONG_PASSWORD, "full_name": "Atomic OK",
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertIn("user_id", resp.json())

        db = _db_session()
        try:
            user = db.query(models.User).filter(models.User.email == "atomic_ok@example.com").first()
            self.assertIsNotNone(user)
            token = db.query(models.VerificationToken).filter(
                models.VerificationToken.user_id == user.user_id
            ).first()
            self.assertIsNotNone(token, "verification token must exist alongside the user row")
        finally:
            db.close()

    def test_failure_adding_verification_token_rolls_back_the_user_too(self):
        """Forces the second db.add() (the VerificationToken) to raise,
        simulating a failure between the two inserts, and proves the User
        row from the SAME request is not left behind -- the core of the
        atomicity fix."""
        from sqlalchemy.orm import Session as SASession
        original_add = SASession.add

        def failing_add(self_session, instance, *a, **kw):
            if isinstance(instance, models.VerificationToken):
                raise RuntimeError("simulated failure adding verification token")
            return original_add(self_session, instance, *a, **kw)

        # This test's isolated app (unlike the real main.py) has no
        # catch-all Exception handler registered, so TestClient's default
        # raise_server_exceptions=True re-raises the error into the test
        # itself rather than returning a 500 -- in the real app, main.py's
        # own catch-all handler converts this to a generic 500 with no
        # leaked detail (already covered by main.py's own exception-handler
        # tests elsewhere); what THIS test is proving is the DB-level
        # rollback guarantee, independent of how the error surfaces to the
        # client.
        with patch.object(SASession, "add", failing_add):
            with self.assertRaises(RuntimeError):
                self.client.post("/api/auth/register", json={
                    "email": "atomic_fail@example.com", "password": STRONG_PASSWORD,
                })

        # Verify via a completely FRESH session/connection (not the one the
        # failed request used) that NEITHER row was persisted.
        db = _db_session()
        try:
            user = db.query(models.User).filter(models.User.email == "atomic_fail@example.com").first()
            self.assertIsNone(user, "a failure adding the verification token must roll back the user row too")
            token_count = db.query(models.VerificationToken).count()
            self.assertEqual(token_count, 0)
        finally:
            db.close()


class TestLoginEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = _fresh_client()
        self.db = _db_session()
        self.user = _make_user(self.db, email="login@example.com")

    def tearDown(self):
        self.db.close()

    def test_login_success_returns_token_and_user(self):
        resp = self.client.post("/api/auth/login", json={
            "email": "login@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertTrue(len(body["access_token"]) > 20)
        self.assertEqual(body["user"]["email"], "login@example.com")

        payload = auth.decode_token(body["access_token"])
        self.assertEqual(payload["email"], "login@example.com")

    def test_login_wrong_password_rejected(self):
        resp = self.client.post("/api/auth/login", json={
            "email": "login@example.com", "password": "WrongPassword123!",
        })
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid email or password", resp.json()["detail"])

    def test_login_unknown_email_rejected_generic_message(self):
        # Must not reveal whether the email exists.
        resp = self.client.post("/api/auth/login", json={
            "email": "nobody@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid email or password", resp.json()["detail"])

    def test_login_records_audit_log_on_success_and_failure(self):
        self.client.post("/api/auth/login", json={
            "email": "login@example.com", "password": "WrongPassword123!",
        })
        self.client.post("/api/auth/login", json={
            "email": "login@example.com", "password": STRONG_PASSWORD,
        })
        db = _db_session()
        try:
            attempts = db.query(models.LoginAttempt).filter(
                models.LoginAttempt.email == "login@example.com"
            ).order_by(models.LoginAttempt.id).all()
            self.assertEqual(len(attempts), 2)
            self.assertFalse(attempts[0].success)
            self.assertEqual(attempts[0].failure_reason, "Invalid credentials")
            self.assertTrue(attempts[1].success)
        finally:
            db.close()

    def test_account_locks_after_five_failed_logins(self):
        for _ in range(5):
            resp = self.client.post("/api/auth/login", json={
                "email": "login@example.com", "password": "WrongPassword123!",
            })
            self.assertEqual(resp.status_code, 401)

        # 6th attempt, even with the CORRECT password, must be locked out.
        resp = self.client.post("/api/auth/login", json={
            "email": "login@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(resp.status_code, 403)
        self.assertIn("temporarily locked", resp.json()["detail"])

    def test_successful_login_resets_failed_attempt_counter(self):
        for _ in range(3):
            self.client.post("/api/auth/login", json={
                "email": "login@example.com", "password": "WrongPassword123!",
            })
        resp = self.client.post("/api/auth/login", json={
            "email": "login@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(resp.status_code, 200)

        db = _db_session()
        try:
            user = db.query(models.User).filter(models.User.email == "login@example.com").first()
            self.assertEqual(user.failed_login_attempts, 0)
            self.assertIsNotNone(user.last_login)
        finally:
            db.close()

    def test_inactive_account_rejected(self):
        db = _db_session()
        _make_user(db, email="inactive@example.com", is_active=False)
        db.close()

        resp = self.client.post("/api/auth/login", json={
            "email": "inactive@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(resp.status_code, 401)
        self.assertIn("disabled", resp.json()["detail"])


class TestGetCurrentUserDependency(unittest.TestCase):
    """Exercises auth.get_current_user via the router's own GET /me route."""

    def setUp(self):
        self.client = _fresh_client()
        self.db = _db_session()
        self.user = _make_user(self.db, email="me@example.com")

    def tearDown(self):
        self.db.close()

    def _login_token(self):
        resp = self.client.post("/api/auth/login", json={
            "email": "me@example.com", "password": STRONG_PASSWORD,
        })
        return resp.json()["access_token"]

    def test_valid_token_returns_profile(self):
        token = self._login_token()
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], "me@example.com")

    def test_missing_token_rejected(self):
        resp = self.client.get("/api/auth/me")
        self.assertIn(resp.status_code, (401, 403))

    def test_malformed_token_rejected(self):
        resp = self.client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
        self.assertEqual(resp.status_code, 401)

    def test_blacklisted_token_rejected(self):
        token = self._login_token()
        payload = auth.decode_token(token)
        db = _db_session()
        try:
            auth.blacklist_token(db, payload["jti"])
        finally:
            db.close()

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("revoked", resp.json()["detail"])

    def test_token_for_deactivated_user_rejected(self):
        token = self._login_token()
        db = _db_session()
        try:
            user = db.query(models.User).filter(models.User.email == "me@example.com").first()
            user.is_active = False
            db.commit()
        finally:
            db.close()

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 403)


class TestVerifyEmailEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = _fresh_client()
        self._send_email_patch = patch.object(auth, "send_email")
        self._send_email_patch.start()

        self.db = _db_session()
        self.user = _make_user(self.db, email="verify@example.com", is_verified=False)
        self.token = models.VerificationToken(
            user_id=self.user.user_id,
            token_hash="tok-hash-abc",
            otp_code="123456",
            token_type="email_verification",
            expires_at=get_ist_now() + timedelta(hours=24),
        )
        self.db.add(self.token)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self._send_email_patch.stop()

    def test_verify_with_correct_otp(self):
        resp = self.client.post("/api/auth/verify-email", json={"otp": "123456"})
        self.assertEqual(resp.status_code, 200, resp.text)

        db = _db_session()
        try:
            user = db.query(models.User).filter(models.User.email == "verify@example.com").first()
            self.assertTrue(user.is_verified)
        finally:
            db.close()

    def test_verify_with_correct_token(self):
        resp = self.client.post("/api/auth/verify-email", json={"token": "tok-hash-abc"})
        self.assertEqual(resp.status_code, 200)

    def test_verify_with_wrong_otp_rejected(self):
        resp = self.client.post("/api/auth/verify-email", json={"otp": "000000"})
        self.assertEqual(resp.status_code, 400)

    def test_verify_with_neither_otp_nor_token_rejected(self):
        resp = self.client.post("/api/auth/verify-email", json={})
        self.assertEqual(resp.status_code, 400)

    def test_used_token_cannot_be_reused(self):
        first = self.client.post("/api/auth/verify-email", json={"otp": "123456"})
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/api/auth/verify-email", json={"otp": "123456"})
        self.assertEqual(second.status_code, 400)

    def test_otp_brute_force_rate_limited_per_ip(self):
        # _OTP_MAX_ATTEMPTS = 10 within a 5-minute window, per client IP.
        for _ in range(10):
            resp = self.client.post("/api/auth/verify-email", json={"otp": "000000"})
            self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/auth/verify-email", json={"otp": "000000"})
        self.assertEqual(resp.status_code, 429)


class TestForgotAndResetPassword(unittest.TestCase):
    def setUp(self):
        self.client = _fresh_client()
        self._send_email_patch = patch.object(auth, "send_email")
        self._send_email_patch.start()

        self.db = _db_session()
        self.user = _make_user(self.db, email="reset@example.com")

    def tearDown(self):
        self.db.close()
        self._send_email_patch.stop()

    def test_forgot_password_creates_reset_token(self):
        resp = self.client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        self.assertEqual(resp.status_code, 200)

        db = _db_session()
        try:
            token = db.query(models.VerificationToken).filter(
                models.VerificationToken.user_id == self.user.user_id,
                models.VerificationToken.token_type == "password_reset",
            ).first()
            self.assertIsNotNone(token)
        finally:
            db.close()

    def test_forgot_password_unknown_email_does_not_leak(self):
        resp = self.client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
        # Same generic message regardless of whether the account exists.
        self.assertEqual(resp.status_code, 200)
        self.assertIn("If that email is registered", resp.json()["message"])

    def test_reset_password_with_valid_token_changes_password(self):
        self.client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        db = _db_session()
        try:
            token_record = db.query(models.VerificationToken).filter(
                models.VerificationToken.user_id == self.user.user_id,
                models.VerificationToken.token_type == "password_reset",
            ).first()
            plain_token = token_record.token_hash
        finally:
            db.close()

        new_password = "NewStr0ng!Passw0rd"
        resp = self.client.post("/api/auth/reset-password", json={
            "token": plain_token, "new_password": new_password,
        })
        self.assertEqual(resp.status_code, 200, resp.text)

        login_resp = self.client.post("/api/auth/login", json={
            "email": "reset@example.com", "password": new_password,
        })
        self.assertEqual(login_resp.status_code, 200)

        old_password_login = self.client.post("/api/auth/login", json={
            "email": "reset@example.com", "password": STRONG_PASSWORD,
        })
        self.assertEqual(old_password_login.status_code, 401)

    def test_reset_password_with_invalid_token_rejected(self):
        resp = self.client.post("/api/auth/reset-password", json={
            "token": "not-a-real-token", "new_password": "NewStr0ng!Passw0rd",
        })
        self.assertEqual(resp.status_code, 400)

    def test_reset_password_weak_new_password_rejected(self):
        self.client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        db = _db_session()
        try:
            token_record = db.query(models.VerificationToken).filter(
                models.VerificationToken.user_id == self.user.user_id,
                models.VerificationToken.token_type == "password_reset",
            ).first()
            plain_token = token_record.token_hash
        finally:
            db.close()

        resp = self.client.post("/api/auth/reset-password", json={
            "token": plain_token, "new_password": "weak",
        })
        self.assertEqual(resp.status_code, 400)

    def test_reset_token_single_use(self):
        self.client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        db = _db_session()
        try:
            token_record = db.query(models.VerificationToken).filter(
                models.VerificationToken.user_id == self.user.user_id,
                models.VerificationToken.token_type == "password_reset",
            ).first()
            plain_token = token_record.token_hash
        finally:
            db.close()

        first = self.client.post("/api/auth/reset-password", json={
            "token": plain_token, "new_password": "NewStr0ng!Passw0rd",
        })
        self.assertEqual(first.status_code, 200)

        second = self.client.post("/api/auth/reset-password", json={
            "token": plain_token, "new_password": "AnotherStr0ng!Pass",
        })
        self.assertEqual(second.status_code, 400)


class TestResendVerification(unittest.TestCase):
    def setUp(self):
        self.client = _fresh_client()
        self._send_email_patch = patch.object(auth, "send_email")
        self._send_email_patch.start()

    def tearDown(self):
        self._send_email_patch.stop()

    def test_resend_for_unverified_user_creates_new_token(self):
        db = _db_session()
        _make_user(db, email="unverified@example.com", is_verified=False)
        db.close()

        resp = self.client.post("/api/auth/resend-verification", json={"email": "unverified@example.com"})
        self.assertEqual(resp.status_code, 200)

        db = _db_session()
        try:
            user = db.query(models.User).filter(models.User.email == "unverified@example.com").first()
            count = db.query(models.VerificationToken).filter(
                models.VerificationToken.user_id == user.user_id
            ).count()
            self.assertEqual(count, 1)
        finally:
            db.close()

    def test_resend_for_already_verified_user_short_circuits(self):
        db = _db_session()
        _make_user(db, email="already@example.com", is_verified=True)
        db.close()

        resp = self.client.post("/api/auth/resend-verification", json={"email": "already@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already verified", resp.json()["message"])

    def test_resend_unknown_email_does_not_leak(self):
        resp = self.client.post("/api/auth/resend-verification", json={"email": "ghost@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("If that email is registered", resp.json()["message"])


if __name__ == "__main__":
    unittest.main()
