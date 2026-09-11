"""Tests for the JWT SECRET KEY PRODUCTION VALIDATION phase.

auth.py's existing "raise RuntimeError if JWT_SECRET_KEY is missing" check
(BUG-11) is real but insufficient on its own: the placeholder shipped in
every .env.example in this repo ("change_me_to_a_random_64_char_hex_string")
is 40 characters -- long enough to pass a naive length check -- so an
operator who copies .env.example verbatim into backend/.env would have
started production with a publicly-known signing secret. This phase
extends the SAME existing validation (in auth.py, at module-import time,
where it already lived) to also reject that exact placeholder and any
secret under 32 characters, and to reject (not silently strip)
leading/trailing whitespace.

This validation runs at MODULE IMPORT time (auth.py's top level), not
inside a function -- the only faithful way to test each rejection path is
to actually import auth.py in a fresh subprocess with a controlled
JWT_SECRET_KEY, exactly as a real `gunicorn main:app` startup would. This
mirrors the established pattern in tests/test_migrate_and_verify.py for
the same reason (subprocess-level, "would this really stop startup"
proof, not a mocked substitute).

Every subprocess sets JWT_SECRET_KEY to an explicit value (never removes
it from the environment) -- auth.py's own load_dotenv() has
override=False semantics: an explicitly-empty value already counts as
"present" and is left alone, but a truly-absent one would be silently
refilled from this dev machine's real backend/.env, defeating the
missing/empty test cases. See test_migrate_and_verify.py's own
docstring for the identical reasoning.
"""
import os
import subprocess
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PLACEHOLDER = "change_me_to_a_random_64_char_hex_string"
_MIN_LENGTH = 32


def _import_auth_with_secret(secret_value: str, extra_code: str = "") -> subprocess.CompletedProcess:
    """Runs `python -c "import auth"` (+ optional extra_code) from backend/
    with JWT_SECRET_KEY set to secret_value, proving the real module-level
    validation that a real startup would hit."""
    env = dict(os.environ)
    env["JWT_SECRET_KEY"] = secret_value
    code = "import auth\n" + extra_code
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=30,
    )


class MissingOrEmptySecret(unittest.TestCase):
    """Requirement A -- pre-existing behavior, must remain intact."""

    def test_empty_string_secret_fails_startup(self):
        # os.getenv("JWT_SECRET_KEY", "") treats an explicitly-empty value
        # identically to a genuinely-absent one -- both exercise this path.
        result = _import_auth_with_secret("")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JWT_SECRET_KEY environment variable is not set", result.stderr)

    def test_whitespace_only_secret_is_rejected(self):
        """A secret that's only whitespace is non-empty (truthy) but is
        indistinguishable from misconfiguration -- must be caught by the
        whitespace check, not silently accepted as a 4-space "secret"."""
        result = _import_auth_with_secret("    ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("whitespace", result.stderr)


class KnownPlaceholder(unittest.TestCase):
    """Requirement B."""

    def test_exact_env_example_placeholder_fails_startup(self):
        result = _import_auth_with_secret(_PLACEHOLDER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("placeholder", result.stderr)

    def test_placeholder_is_long_enough_to_otherwise_pass_a_naive_length_check(self):
        """Documents WHY a dedicated placeholder check is required at all --
        if this assertion ever fails, the length-only check might become
        sufficient on its own, but that's not the case today."""
        self.assertGreaterEqual(len(_PLACEHOLDER), _MIN_LENGTH)


class WeakSecret(unittest.TestCase):
    """Requirement C."""

    def test_short_secret_fails_startup(self):
        short = "a" * (_MIN_LENGTH - 1)
        result = _import_auth_with_secret(short)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at least", result.stderr)
        self.assertIn(str(_MIN_LENGTH), result.stderr)

    def test_one_character_secret_fails_startup(self):
        result = _import_auth_with_secret("x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at least", result.stderr)

    def test_exactly_minimum_length_secret_succeeds(self):
        exact = "b" * _MIN_LENGTH
        result = _import_auth_with_secret(exact)
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")

    def test_one_below_minimum_length_fails(self):
        result = _import_auth_with_secret("c" * (_MIN_LENGTH - 1))
        self.assertNotEqual(result.returncode, 0)


class WhitespaceHandling(unittest.TestCase):
    """Requirement D -- reject, do not silently normalize."""

    def test_leading_whitespace_rejected(self):
        result = _import_auth_with_secret(" " + "d" * _MIN_LENGTH)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("whitespace", result.stderr)

    def test_trailing_whitespace_rejected(self):
        result = _import_auth_with_secret("d" * _MIN_LENGTH + " ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("whitespace", result.stderr)

    def test_trailing_newline_rejected(self):
        """A common real-world source: a Docker/K8s secret file or a .env
        line with a trailing newline baked into the value."""
        result = _import_auth_with_secret("e" * _MIN_LENGTH + "\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("whitespace", result.stderr)

    def test_internal_whitespace_is_not_rejected(self):
        """Only LEADING/TRAILING whitespace is a signal of accidental
        padding -- a secret with internal spaces is unusual but not the
        failure mode this check targets, and rejecting it would be scope
        creep beyond what was asked."""
        internal = ("f" * 10) + " " + ("f" * 21)  # 32 chars total, no lead/trail whitespace
        self.assertEqual(len(internal), _MIN_LENGTH)
        result = _import_auth_with_secret(internal)
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")


class ValidSecretStillWorks(unittest.TestCase):
    """Requirement 7 (tests section) -- valid secret continues to
    generate/verify JWTs exactly as before, end-to-end in the same
    subprocess that passed the new startup gate."""

    def test_strong_random_looking_secret_succeeds_and_round_trips_a_token(self):
        import secrets as _secrets
        strong_secret = _secrets.token_hex(32)  # 64 hex chars, matches the documented generation command
        code = (
            "from datetime import timedelta\n"
            "token = auth.create_access_token({'sub': 'user123'}, timedelta(hours=1))\n"
            "assert isinstance(token, str) and token, 'token must be a non-empty string'\n"
            "from jose import jwt\n"
            "payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])\n"
            "assert payload['sub'] == 'user123'\n"
            "print('ROUNDTRIP_OK')\n"
        )
        result = _import_auth_with_secret(strong_secret, extra_code=code)
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn("ROUNDTRIP_OK", result.stdout)

    def test_algorithm_and_expiration_unchanged(self):
        code = (
            "assert auth.ALGORITHM == 'HS256', auth.ALGORITHM\n"
            "assert auth.ACCESS_TOKEN_EXPIRE_HOURS == 24, auth.ACCESS_TOKEN_EXPIRE_HOURS\n"
            "print('UNCHANGED_OK')\n"
        )
        result = _import_auth_with_secret("g" * _MIN_LENGTH, extra_code=code)
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn("UNCHANGED_OK", result.stdout)


class SecretNeverLeaked(unittest.TestCase):
    """Requirement E / security review -- error output must never contain
    the actual secret value."""

    def test_short_secret_value_itself_never_appears_in_error_output(self):
        secret = "sUp3r_uniqu3_short_marker_XyZ"  # under the 32-char minimum, deliberately
        self.assertLess(len(secret), _MIN_LENGTH)
        result = _import_auth_with_secret(secret)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)

    def test_whitespace_padded_secret_value_never_appears_in_error_output(self):
        secret = " uniqueWhitespaceMarkerAbc123 "
        result = _import_auth_with_secret(secret)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(secret.strip(), result.stdout)
        self.assertNotIn(secret.strip(), result.stderr)


if __name__ == "__main__":
    unittest.main()
