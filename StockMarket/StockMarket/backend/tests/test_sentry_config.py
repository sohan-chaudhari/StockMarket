"""Proves main.py's sentry_sdk.init() call uses the safe production default
for send_default_pii, without importing main.py (which has expensive
import-time side effects: Angel One login, DB migrations, ~22 background
tasks -- the same reason every other test file touching main.py-adjacent
config uses a static/mirrored check instead of a real import; see
test_monitoring.py, test_rate_limiting.py, test_dashboard_websocket.py).

Parses main.py's AST directly and inspects the literal keyword arguments of
the actual sentry_sdk.init(...) call -- robust to comment/formatting
changes, and doesn't require executing any of the module's import-time
side effects.

HARDEN-01: send_default_pii=True made the SDK attach request bodies,
cookies, and the caller's IP address to every captured event by default --
a real PII exposure to a third-party SaaS for an app handling login
credentials and trading actions. Fixed to False.
"""
import ast
import os
import unittest

MAIN_PY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


def _find_sentry_init_kwargs():
    """Parses main.py and returns {kwarg_name: literal_value} for the
    sentry_sdk.init(...) call. Non-literal kwargs (e.g. the before_send
    lambda, the integrations list) are returned as None -- only literal
    (str/bool/int/etc.) values are meaningfully checkable this way, which is
    exactly what this test needs for a boolean flag like send_default_pii.
    """
    with open(MAIN_PY_PATH, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=MAIN_PY_PATH)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "init"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sentry_sdk"
        ):
            result = {}
            for kw in node.keywords:
                if kw.arg is None:
                    continue  # **kwargs spread, not relevant here
                try:
                    result[kw.arg] = ast.literal_eval(kw.value)
                except ValueError:
                    result[kw.arg] = None  # non-literal (lambda, list, etc.)
            return result
    return None


class TestSentryPiiConfig(unittest.TestCase):
    def setUp(self):
        self.kwargs = _find_sentry_init_kwargs()

    def test_sentry_init_call_is_found(self):
        self.assertIsNotNone(self.kwargs, "sentry_sdk.init(...) call not found in main.py")

    def test_send_default_pii_is_false(self):
        self.assertIn("send_default_pii", self.kwargs)
        self.assertIs(
            self.kwargs["send_default_pii"], False,
            "send_default_pii must default to False in production -- it controls whether "
            "request bodies, cookies, and the caller's IP are attached to every Sentry event",
        )

    def test_dsn_and_traces_sample_rate_unchanged(self):
        """This fix must not touch unrelated Sentry behavior."""
        self.assertIn("dsn", self.kwargs)
        self.assertTrue(str(self.kwargs["dsn"]).startswith("https://"))
        self.assertEqual(self.kwargs.get("traces_sample_rate"), 0.1)


if __name__ == "__main__":
    unittest.main()
