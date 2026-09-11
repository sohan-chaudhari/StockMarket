"""SEC-01 regression tests.

Proves the shared `rate_limiter.limiter` singleton actually enforces limits
end-to-end (429 once exceeded, success while under the limit), not just that
the `@limiter.limit(...)` decorators are present and importable.

Uses a small isolated FastAPI app (not the real main.py app) reusing the
SAME shared limiter instance that main.py and the routers import, so the
result is representative of production wiring without paying the cost of
main.py's heavy startup (Angel One login, DB migrations, background
threads).
"""
import unittest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from rate_limiter import limiter

# Built once at module scope (not per-test): slowapi's Limiter keeps a
# `_route_limits` registry keyed by the decorated function's fully-qualified
# name, and that registry is only ever appended to, never cleared by
# `limiter.reset()` (reset() clears counter storage only). Re-decorating a
# same-named route in every setUp() -- as an earlier version of this file
# did -- silently duplicates its Limit entry on each call, so every request
# increments the counter once per accumulated duplicate instead of once.
# Defining the routes exactly once avoids that entirely and matches how the
# real app decorates each endpoint exactly once at import time.
app = FastAPI()
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


@app.get("/probe")
@limiter.limit("3/hour")
def probe(request: Request):
    return {"ok": True}


@app.get("/other")
@limiter.limit("2/hour")
def other(request: Request):
    return {"ok": True}


class TestRateLimiterWiring(unittest.TestCase):
    def setUp(self):
        limiter.reset()
        self.client = TestClient(app)

    def tearDown(self):
        limiter.reset()

    def test_requests_within_limit_succeed(self):
        for _ in range(3):
            resp = self.client.get("/probe")
            self.assertEqual(resp.status_code, 200)

    def test_request_exceeding_limit_is_rejected(self):
        for _ in range(3):
            self.client.get("/probe")
        resp = self.client.get("/probe")
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Too many requests", resp.json()["detail"])

    def test_limits_are_independent_per_route(self):
        # Exhaust /probe (3/hour) -- /other (2/hour) must be unaffected.
        for _ in range(3):
            self.client.get("/probe")
        self.assertEqual(self.client.get("/probe").status_code, 429)
        self.assertEqual(self.client.get("/other").status_code, 200)

    def test_limit_resets_between_tests_via_setup(self):
        # If limiter.reset() in setUp didn't work, this would immediately
        # 429 from state leaked out of the previous test methods above.
        resp = self.client.get("/probe")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
