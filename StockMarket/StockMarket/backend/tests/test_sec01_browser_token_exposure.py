"""SEC-01 regression: the browser must not receive or persist a JWT.

Before this change /api/auth/login returned the JWT in the JSON body and the
frontend persisted it in sessionStorage/localStorage, which defeated the
HttpOnly session cookie (any XSS could read the token and keep an account
takeover across browser restarts).

Locked in here:
  * login/register responses expose NO JWT to browsers;
  * the HttpOnly session cookie carries the intended attributes;
  * cookie-only browser auth works, including the 'cookie-session' marker;
  * non-browser clients can still obtain/use a bearer token via opt-in;
  * unauthenticated / expired / revoked / invalid sessions are rejected;
  * logout revokes (blacklist) and clears the cookie;
  * cookie-authenticated state-changing requests still require CSRF;
  * the shipped frontend no longer writes/reads a JWT in browser storage;
  * SEC-01 follow-up: browser-origin requests cannot obtain a JWT by sending
    the `X-Auth-Mode: bearer` opt-in (that header is not a trust boundary).

Isolated app + in-memory SQLite (same pattern as test_auth.py /
test_auth_session_cookie.py) -- no Postgres, no network.
"""
import os
import unittest
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-auth-tests-only")

from fastapi import FastAPI, Depends, Request
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

PASSWORD = "Str0ng!Passw0rd123"
EMAIL = "sec01@example.com"

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

OPT_IN = {"X-Auth-Mode": "bearer"}

# Headers a browser attaches automatically and page JavaScript cannot set,
# change or remove. A real non-browser client sends none of them.
BROWSER_HEADERS = {
    "Origin": "https://leveragx.tech",
    "Referer": "https://leveragx.tech/login.html",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
}
BROWSER_SIGNALS = [
    ("origin", {"Origin": "https://leveragx.tech"}),
    ("referer", {"Referer": "https://leveragx.tech/login.html"}),
    ("sec-fetch-site", {"Sec-Fetch-Site": "same-origin"}),
    ("sec-fetch-mode", {"Sec-Fetch-Mode": "cors"}),
]


def _client(base_url="http://testserver"):
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    limiter.reset()
    auth_router._otp_attempts.clear()
    db = _SessionLocal()
    try:
        db.add(models.User(
            email=EMAIL,
            password_hash=auth.get_password_hash(PASSWORD),
            full_name="Sec01 User",
            is_active=True,
            is_verified=True,
        ))
        db.commit()
    finally:
        db.close()
    return TestClient(app, base_url=base_url)


def _login(client, headers=None):
    return client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers=headers or {},
    )


def _cookie_attrs(resp):
    raw = resp.headers.get("set-cookie", "")
    return raw, [a.strip().lower() for a in raw.split(";")]


class BrowserJwtExposureTests(unittest.TestCase):

    def test_login_body_exposes_no_jwt(self):
        client = _client()
        resp = _login(client)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIsNone(body.get("access_token"))
        self.assertEqual(body["user"]["email"], EMAIL)
        # No JWT-looking value anywhere in the body (Set-Cookie is a header).
        self.assertNotIn("eyJ", resp.text)

    def test_register_body_exposes_no_jwt(self):
        client = _client()
        resp = client.post("/api/auth/register", json={
            "email": "brand-new@example.com", "password": PASSWORD, "full_name": "Brand New",
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertNotIn("eyJ", resp.text)
        self.assertNotIn("access_token", resp.text)

    def test_opt_in_still_yields_a_usable_bearer_token(self):
        client = _client()
        resp = _login(client, OPT_IN)
        token = resp.json()["access_token"]
        self.assertTrue(len(token) > 20)
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], EMAIL)

    def test_session_cookie_attributes_over_http(self):
        client = _client()  # http
        _, attrs = _cookie_attrs(_login(client))
        self.assertTrue(any(a.startswith(auth.AUTH_COOKIE_NAME + "=") for a in attrs))
        self.assertTrue(any(a == "httponly" for a in attrs))
        self.assertTrue(any(a == "samesite=lax" for a in attrs))
        self.assertTrue(any(a.startswith("max-age=") for a in attrs))
        self.assertTrue(any(a == "path=/" for a in attrs))
        # Plain HTTP must not be Secure, or the browser would drop the cookie.
        self.assertFalse(any(a == "secure" for a in attrs))

    def test_session_cookie_is_secure_over_https(self):
        # nginx terminates TLS and sets X-Forwarded-Proto: https, which the
        # UvicornWorker trusts from 127.0.0.1, so request.url.scheme is https.
        client = _client(base_url="https://testserver")
        _, attrs = _cookie_attrs(_login(client))
        self.assertTrue(any(a == "secure" for a in attrs))

    def test_cookie_authenticates_browser_without_bearer(self):
        client = _client()
        _login(client)
        resp = client.get("/api/auth/me")  # cookie jar only, no Authorization
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], EMAIL)

    def test_unauthenticated_request_is_rejected(self):
        client = _client()
        self.assertIn(client.get("/api/auth/me").status_code, (401, 403))

    def test_sentinel_marker_falls_back_to_cookie(self):
        client = _client()
        _login(client)
        # The frontend sends this benign marker; the cookie must still win.
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer cookie-session"})
        self.assertEqual(resp.status_code, 200)

    def test_invalid_and_expired_sessions_rejected(self):
        client = _client()
        self.assertEqual(
            client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code,
            401,
        )
        expired = auth.create_access_token(
            {"user_id": 1, "email": EMAIL}, expires_delta=timedelta(seconds=-60))
        self.assertEqual(
            client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code,
            401,
        )

    def test_logout_revokes_and_clears_cookie(self):
        client = _client()
        _login(client)
        cookie = client.cookies.get(auth.AUTH_COOKIE_NAME)
        self.assertTrue(cookie)

        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        # Session is gone: cookie cleared and/or jti blacklisted.
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

        payload = auth.decode_token(cookie)
        db = _SessionLocal()
        try:
            self.assertTrue(auth.is_token_blacklisted(db, payload["jti"]))
        finally:
            db.close()

        # Replaying the revoked token is rejected too.
        self.assertEqual(
            client.get("/api/auth/me", headers={"Authorization": f"Bearer {cookie}"}).status_code,
            401,
        )


class BrowserOriginCannotOptInTests(unittest.TestCase):
    """SEC-01 follow-up: `X-Auth-Mode` is not a trust boundary.

    Any same-origin page script (including injected/XSS) can set that header,
    so it cannot by itself prove a request came from a non-browser client.
    Browser-controlled Fetch-metadata headers -- which script cannot forge or
    strip -- must therefore suppress the JWT in the body as well. A normal
    machine client (curl/requests/SDK) sends none of them and keeps working.
    """

    def test_full_browser_login_with_opt_in_still_gets_no_jwt(self):
        client = _client()
        headers = dict(BROWSER_HEADERS)
        headers.update(OPT_IN)
        resp = _login(client, headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json().get("access_token"))
        self.assertNotIn("eyJ", resp.text)

    def test_each_browser_signal_blocks_the_body_token(self):
        for name, signal in BROWSER_SIGNALS:
            with self.subTest(signal=name):
                client = _client()
                headers = dict(signal)
                headers.update(OPT_IN)
                resp = _login(client, headers)
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertIsNone(
                    resp.json().get("access_token"),
                    f"a browser request carrying {name} must not receive a JWT",
                )
                self.assertNotIn("eyJ", resp.text)

    def test_browser_signal_without_opt_in_also_gets_no_jwt(self):
        client = _client()
        resp = _login(client, BROWSER_HEADERS)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json().get("access_token"))

    def test_browser_request_can_still_authenticate_via_cookie(self):
        # Locking the body token out must not disturb the cookie flow.
        client = _client()
        resp = _login(client, BROWSER_HEADERS)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(client.get("/api/auth/me").status_code, 200)

    def test_register_does_not_return_a_jwt_even_with_opt_in(self):
        client = _client()
        headers = dict(BROWSER_HEADERS)
        headers.update(OPT_IN)
        resp = client.post(
            "/api/auth/register",
            json={"email": "no-jwt@example.com", "password": PASSWORD, "full_name": "No JWT"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertNotIn("eyJ", resp.text)
        self.assertNotIn("access_token", resp.text)

    def test_non_browser_client_without_browser_headers_is_supported(self):
        # The supported machine-client contract: opt-in header, no browser
        # headers -> a usable bearer token.
        client = _client()
        resp = _login(client, OPT_IN)
        token = resp.json()["access_token"]
        self.assertTrue(len(token) > 20)
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)


class CsrfStillEnforcedTests(unittest.TestCase):
    """SEC-01 must not weaken CSRF for cookie-authenticated requests.

    Exercises the REAL dependency from main.py (imported lazily -- importing
    main is heavy but is done throughout this suite)."""

    def test_real_csrf_dependency_unchanged(self):
        import main as real_main

        csrf_app = FastAPI()

        @csrf_app.post("/protected", dependencies=[Depends(real_main.validate_csrf)])
        def _protected():
            return {"ok": True}

        client = TestClient(csrf_app)

        # No CSRF cookie/header -> blocked.
        self.assertEqual(client.post("/protected").status_code, 403)

        # Cookie only -> blocked.
        client.cookies.set("csrf_token", "abc123")
        self.assertEqual(client.post("/protected").status_code, 403)

        # Cookie + matching header -> allowed.
        self.assertEqual(
            client.post("/protected", headers={"X-CSRF-Token": "abc123"}).status_code, 200)

        # Mismatch -> blocked.
        self.assertEqual(
            client.post("/protected", headers={"X-CSRF-Token": "nope"}).status_code, 403)


class FrontendDoesNotPersistJwtTests(unittest.TestCase):
    """The shipped frontend must no longer write/read a JWT in JS storage."""

    def test_no_frontend_file_references_access_token(self):
        offenders = []
        for pattern in ("*.html", "*.js"):
            for p in FRONTEND.glob(pattern):
                if "access_token" in p.read_text(encoding="utf-8", errors="replace"):
                    offenders.append(p.name)
        self.assertEqual(offenders, [], f"files still referencing access_token: {offenders}")

    def test_login_stores_only_the_marker(self):
        t = (FRONTEND / "login.html").read_text(encoding="utf-8", errors="replace")
        self.assertIn("sessionStorage.setItem('token', 'cookie-session')", t)
        self.assertIn("localStorage.setItem('token', 'cookie-session')", t)

    def test_user_ws_does_not_send_a_jwt(self):
        t = (FRONTEND / "stock-logic.js").read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("JSON.stringify({ token: token })", t)


if __name__ == "__main__":
    unittest.main()
