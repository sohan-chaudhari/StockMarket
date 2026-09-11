"""Proves the three POST-LOAD MEMORY INVESTIGATION config changes are
present and correctly valued, without booting the real app (main.py has
expensive import-time side effects -- same reasoning as test_sentry_config.py
and test_database_engine_config.py).

Root cause this phase addressed: under combined API+WebSocket load, process
RSS grew far more than tracemalloc's own tracked-Python-object view did
(measured live: traced objects +~94MB across an idle->mixed-load sequence
where RSS grew +~400MB in the same sequence) -- the gap tracks tightly with
OS thread-count spikes, not object retention. Every synchronous (`def`, not
`async def`) FastAPI route (chart/intraday/all-stocks among the busiest --
they use SQLAlchemy's sync Session and can't be trivially made async)
dispatches to AnyIO's default thread pool (up to 40 concurrent threads by
default), and glibc gives each thread its own malloc arena that is never
released even once idle.

Three changes, all pure config/tuning -- zero route or behavior change:
  1. MALLOC_ARENA_MAX=2 (Dockerfile) -- bounds how many arenas glibc will
     create at all, forcing thread reuse of a small fixed set instead of
     growing one per concurrent thread.
  2. AnyIO thread-limiter capped to 8 (main.py startup()) -- bounds how many
     OS threads sync routes can occupy concurrently in the first place.
  3. gunicorn max_requests/max_requests_jitter -- a backstop worker recycle
     in case of any residual very-long-uptime growth; NOT a fix for the root
     cause (the other two are), so this is intentionally a high threshold
     that should rarely fire under normal load.
"""
import ast
import os
import re
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PY_PATH = os.path.join(BACKEND_DIR, "main.py")
DOCKERFILE_PATH = os.path.join(BACKEND_DIR, "Dockerfile")
GUNICORN_CONF_PATH = os.path.join(BACKEND_DIR, "gunicorn.conf.py")


class TestMallocArenaMaxInDockerfile(unittest.TestCase):
    def setUp(self):
        with open(DOCKERFILE_PATH, "r", encoding="utf-8") as f:
            self.source = f.read()

    def test_malloc_arena_max_is_set(self):
        self.assertIn("MALLOC_ARENA_MAX=2", self.source)

    def test_malloc_arena_max_is_set_before_the_app_runs(self):
        """Must be an ENV directive (baked into the image, applies to every
        process the container ever runs), not something set only inside
        application Python code -- glibc reads it once at process start,
        before any app code executes, so an app-level os.environ[...] = ...
        would be too late."""
        env_section = self.source[: self.source.find("CMD [")]
        self.assertIn("MALLOC_ARENA_MAX=2", env_section)


class TestAnyioThreadLimiterCap(unittest.TestCase):
    def setUp(self):
        with open(MAIN_PY_PATH, "r", encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=MAIN_PY_PATH)

    def test_thread_limiter_import_present(self):
        self.assertIn("import anyio.to_thread", self.source)

    def test_thread_limiter_capped_to_8(self):
        self.assertIn(
            "anyio.to_thread.current_default_thread_limiter().total_tokens = 8",
            self.source,
        )

    def test_thread_limiter_cap_lives_inside_the_startup_event_handler(self):
        """The cap only takes effect once set on the live event loop's
        limiter instance -- must run inside an async startup hook (where a
        running event loop exists), not at module import time."""
        found_in_startup = False
        for node in ast.walk(self.tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "startup":
                body_src = ast.unparse(node)
                if "current_default_thread_limiter" in body_src:
                    found_in_startup = True
        self.assertTrue(found_in_startup,
                         "thread-limiter cap must be set inside the async startup() event handler")

    def test_startup_is_registered_as_an_on_event_startup_handler(self):
        decorators_found = False
        for node in ast.walk(self.tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "startup":
                for dec in node.decorator_list:
                    dec_src = ast.unparse(dec)
                    if 'on_event("startup")' in dec_src or "on_event('startup')" in dec_src:
                        decorators_found = True
        self.assertTrue(decorators_found)


class TestGunicornWorkerRecycling(unittest.TestCase):
    def setUp(self):
        with open(GUNICORN_CONF_PATH, "r", encoding="utf-8") as f:
            self.source = f.read()

    def _module_level_int(self, name):
        m = re.search(rf"^{name}\s*=\s*(\d+)", self.source, re.MULTILINE)
        self.assertIsNotNone(m, f"{name} not found as a module-level assignment")
        return int(m.group(1))

    def test_max_requests_is_set_and_generous(self):
        """A backstop, not a routine recycle -- must be high enough that it
        essentially never fires under this app's normal request volume
        (tests aren't meant to catch the exact tuned value drifting, just
        that it stays in the 'backstop', not 'routine churn', range)."""
        value = self._module_level_int("max_requests")
        self.assertGreaterEqual(value, 2000)

    def test_max_requests_jitter_is_set_and_nonzero(self):
        """Nonzero jitter is required -- otherwise the single worker
        recycles at an exactly predictable request count every time,
        which (with workers=1) means a fully deterministic, synchronized
        micro-outage window rather than a spread-out one."""
        value = self._module_level_int("max_requests_jitter")
        self.assertGreater(value, 0)
        max_requests = self._module_level_int("max_requests")
        self.assertLess(value, max_requests,
                         "jitter must be smaller than max_requests itself")

    def test_workers_still_hard_pinned_to_one(self):
        """This phase must not have touched the workers=1 architectural
        constraint documented at the top of this file."""
        value = self._module_level_int("workers")
        self.assertEqual(value, 1)


if __name__ == "__main__":
    unittest.main()
