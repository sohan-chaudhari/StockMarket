"""Proves requirements.txt/requirements-dev.txt carry the exact pins decided
in the DEPENDENCY / CVE + SUPPLY-CHAIN SECURITY AUDIT + its remediation phase,
so a future edit can't silently drift away from them without a test failure.

Parses the plain-text manifests directly (they're not Python, so no AST
needed) -- robust to reordering/comment changes, not to the exact line
position of any given pin.

REMEDIATION PHASE:
  - fastapi bumped 0.115.12 -> 0.133.0 (the minimum version that lifts
    FastAPI's own `starlette<1.0.0` ceiling -- earlier versions cannot
    install a patched Starlette at all).
  - starlette pinned explicitly at 1.3.1 (not left to fastapi's own
    unpinned `starlette>=0.40.0` range) -- the minimum version clearing
    every Starlette CVE found in the audit. Starlette backs every FastAPI
    request (server-exposed), unlike nltk/aiohttp/ecdsa, whose vulnerable
    paths were proven unreachable and were deliberately left unchanged.
  - playwright removed entirely -- confirmed unused anywhere in this app's
    own code; its wheel alone bundled a ~138MB Node.js driver binary that
    showed up as a separate, unused attack surface in a container scan.
  - pytest/pytest-asyncio moved into a new requirements-dev.txt -- they
    were previously used by the whole tests/ suite and by CI without being
    declared in any tracked manifest at all.
"""
import os
import re
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS_PATH = os.path.join(BACKEND_DIR, "requirements.txt")
REQUIREMENTS_DEV_PATH = os.path.join(BACKEND_DIR, "requirements-dev.txt")


def _parse_pins(path):
    """Returns {package_name_lowercase: version_string} for every `pkg==ver`
    line in a requirements file. Ignores comments/blank lines."""
    pins = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([A-Za-z0-9_.\-]+)", line)
            if m:
                pins[m.group(1).lower()] = m.group(3)
    return pins


class TestFastApiStarlettePins(unittest.TestCase):
    def setUp(self):
        self.pins = _parse_pins(REQUIREMENTS_PATH)

    def test_fastapi_pinned_at_or_above_the_starlette_ceiling_fix(self):
        self.assertIn("fastapi", self.pins)
        major, minor, patch = (int(p) for p in self.pins["fastapi"].split("."))
        self.assertGreaterEqual(
            (major, minor, patch), (0, 133, 0),
            "fastapi must stay >= 0.133.0 -- earlier versions cap starlette below 1.0.0 "
            "and cannot install a patched version at all",
        )

    def test_starlette_is_pinned_explicitly_not_left_to_fastapis_own_range(self):
        self.assertIn("starlette", self.pins,
                       "starlette must be pinned explicitly for a reproducible, "
                       "security-audited version -- not left to fastapi's own unpinned range")

    def test_starlette_pinned_at_or_above_the_audited_fix_version(self):
        major, minor, patch = (int(p) for p in self.pins["starlette"].split("."))
        self.assertGreaterEqual(
            (major, minor, patch), (1, 3, 1),
            "starlette must stay >= 1.3.1 -- the minimum version clearing every "
            "Starlette CVE found in the dependency audit",
        )


class TestPlaywrightRemoved(unittest.TestCase):
    def test_playwright_not_in_production_requirements(self):
        pins = _parse_pins(REQUIREMENTS_PATH)
        self.assertNotIn("playwright", pins,
                          "playwright is confirmed unused by this app and bundles an "
                          "unused ~138MB Node.js runtime -- must not be reintroduced "
                          "into the production image without a real, justified use")


class TestDevDependenciesDeclaredSeparately(unittest.TestCase):
    def setUp(self):
        self.dev_pins = _parse_pins(REQUIREMENTS_DEV_PATH)
        self.prod_pins = _parse_pins(REQUIREMENTS_PATH)

    def test_requirements_dev_file_exists_and_is_parseable(self):
        self.assertTrue(os.path.isfile(REQUIREMENTS_DEV_PATH))

    def test_pytest_and_pytest_asyncio_declared_in_dev_requirements(self):
        self.assertIn("pytest", self.dev_pins)
        self.assertIn("pytest-asyncio", self.dev_pins)

    def test_test_only_packages_are_not_in_production_requirements(self):
        """Keeps the separation real -- pytest/pytest-asyncio must not also
        leak into the production manifest (that would defeat the point of
        splitting them out and bloat the production image with test tooling)."""
        self.assertNotIn("pytest", self.prod_pins)
        self.assertNotIn("pytest-asyncio", self.prod_pins)


if __name__ == "__main__":
    unittest.main()
