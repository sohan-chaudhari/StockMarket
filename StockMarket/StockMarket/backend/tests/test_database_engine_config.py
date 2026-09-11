"""Proves database.py's engine is constructed with the intended
connect/statement timeout, without changing (and while proving unchanged)
the existing pool configuration.

HARDEN-02: previously no connect/statement timeout at all -- a hung TCP
connect or a runaway query could block a pooled connection indefinitely.
Added connect_timeout=5 (TCP+auth handshake bound) and
statement_timeout=30000ms (per-statement, not per-transaction, bound).

Two complementary approaches:
  1. AST-parse database.py's actual create_engine(...) call (robust to
     comment/formatting changes, proves the literal source is correct).
  2. Import the real `database` module (cheap -- unlike main.py, it has no
     heavy import-time side effects) and inspect the already-constructed
     `database.engine` object's pool settings directly, proving the
     pre-existing pool configuration this batch must not touch is intact.
"""
import ast
import os
import unittest

import database

DATABASE_PY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.py")


def _find_module_level_dict_assignment(tree, name):
    """Returns {key: literal_value_or_None} for a module-level
    `name = {...}` dict-literal assignment (None found -> None returned;
    found but not a dict literal -> {}).

    Evaluates each VALUE independently (like _find_create_engine_kwargs
    does for keyword arguments) rather than ast.literal_eval-ing the whole
    dict at once -- _connect_args's "sslmode" key is DB_SSLMODE, a bare
    Name reference (the whole point: it's read from the environment), and
    literal_eval raises ValueError for the ENTIRE dict if even one value
    isn't a literal. A dynamic value shows up here as None; its mere
    PRESENCE as a key is what these tests actually need to assert."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            if not isinstance(node.value, ast.Dict):
                return {}
            result = {}
            for k, v in zip(node.value.keys, node.value.values):
                try:
                    key = ast.literal_eval(k)
                except ValueError:
                    continue
                try:
                    result[key] = ast.literal_eval(v)
                except ValueError:
                    result[key] = None  # present but a dynamic (non-literal) value
            return result
    return None


def _find_create_engine_kwargs():
    """Parses database.py and returns {kwarg_name: literal_value} for the
    create_engine(...) call.

    TLS-TO-POSTGRES: connect_args is now built as a separate module-level
    `_connect_args = {...}` dict (so DB_SSLMODE/DB_SSLROOTCERT can be
    conditionally added to it) and passed by reference
    (connect_args=_connect_args), not as an inline literal in the
    create_engine(...) call itself -- ast.literal_eval can't resolve a bare
    Name reference, so when that's what we find, this resolves it by
    looking up that name's own module-level assignment instead."""
    with open(DATABASE_PY_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=DATABASE_PY_PATH)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "create_engine":
            result = {}
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if isinstance(kw.value, ast.Name):
                    result[kw.arg] = _find_module_level_dict_assignment(tree, kw.value.id)
                    continue
                try:
                    result[kw.arg] = ast.literal_eval(kw.value)
                except ValueError:
                    result[kw.arg] = None
            return result
    return None


class TestCreateEngineSourceConfig(unittest.TestCase):
    """Source-level check -- proves the literal call in database.py is correct."""

    def setUp(self):
        self.kwargs = _find_create_engine_kwargs()

    def test_create_engine_call_is_found(self):
        self.assertIsNotNone(self.kwargs, "create_engine(...) call not found in database.py")

    def test_connect_args_has_connect_timeout(self):
        self.assertIn("connect_args", self.kwargs)
        self.assertEqual(self.kwargs["connect_args"].get("connect_timeout"), 5)

    def test_connect_args_has_statement_timeout(self):
        options = self.kwargs["connect_args"].get("options", "")
        self.assertIn("statement_timeout=30000", options)

    def test_existing_pool_settings_untouched(self):
        """This batch must not resize the pool or change its other settings."""
        self.assertEqual(self.kwargs.get("pool_size"), 20)
        self.assertEqual(self.kwargs.get("max_overflow"), 30)
        self.assertEqual(self.kwargs.get("pool_timeout"), 10)
        self.assertEqual(self.kwargs.get("pool_recycle"), 3600)
        self.assertIs(self.kwargs.get("pool_pre_ping"), True)


class TestLiveEngineObjectConfig(unittest.TestCase):
    """Object-level check -- proves the actually-constructed engine (what
    the running app really uses) matches, not just the source text."""

    def test_pool_settings_match_on_the_real_engine_object(self):
        pool = database.engine.pool
        self.assertEqual(pool.size(), 20)
        self.assertEqual(pool._max_overflow, 30)
        self.assertEqual(pool._pre_ping, True)
        self.assertEqual(pool._recycle, 3600)

    def test_engine_url_still_points_at_configured_database(self):
        # Doesn't change based on this batch -- just confirms the engine
        # object construction itself didn't silently break.
        self.assertEqual(database.engine.url.drivername, "postgresql")


class TestTlsSslConfigSource(unittest.TestCase):
    """PostgreSQL TLS & network-topology security audit / remediation:
    previously no sslmode was set anywhere -- every connection silently
    relied on libpq's own default (sslmode=prefer: opportunistic
    encryption, falls back to plaintext with no warning if unavailable,
    and never validates the server certificate even when SSL IS
    negotiated). DB_SSLMODE/DB_SSLROOTCERT make this explicit and
    configurable without changing default behavior for any environment
    as configured today."""

    def setUp(self):
        with open(DATABASE_PY_PATH, "r", encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=DATABASE_PY_PATH)
        self.connect_args = _find_module_level_dict_assignment(self.tree, "_connect_args")

    def test_sslmode_key_present_in_connect_args(self):
        self.assertIn("sslmode", self.connect_args)

    def test_sslmode_defaults_to_prefer_in_source(self):
        # DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer") -- the literal
        # default argument must be "prefer" so nothing changes for any
        # environment that hasn't explicitly opted in yet.
        self.assertIn('os.getenv("DB_SSLMODE", "prefer")', self.source)

    def test_sslrootcert_only_added_conditionally_not_unconditionally(self):
        # Must NOT appear as an unconditional key in the base dict literal
        # (that would mean it's always sent, even as an empty string,
        # which some drivers treat differently from "omitted entirely").
        self.assertNotIn("sslrootcert", self.connect_args)
        self.assertIn('if DB_SSLROOTCERT:', self.source)
        self.assertIn('_connect_args["sslrootcert"] = DB_SSLROOTCERT', self.source)

    def test_no_hardcoded_ca_path_or_hostname_was_invented(self):
        """This phase's own instruction: do not invent an RDS CA path or
        hostname. Verifies no suspicious hardcoded-looking path (e.g. a
        literal .pem/.crt path, or 'rds.amazonaws.com') was added instead
        of reading from the environment."""
        for suspicious in (".pem", ".crt", "rds.amazonaws.com", "/etc/ssl/"):
            self.assertNotIn(suspicious, self.source,
                              f"found a hardcoded-looking value ({suspicious!r}) -- CA path/host must come from env vars only")


class TestTlsSslConfigLiveBehavior(unittest.TestCase):
    """Proves the actual DB_SSLMODE/DB_SSLROOTCERT env vars are read
    correctly by the real database module -- via subprocess re-import
    with controlled env vars, since database.py reads them once at
    import time (same reasoning/pattern as test_jwt_secret_key_validation.py)."""

    def _import_database_with_env(self, extra_env, extra_code=""):
        import subprocess
        import sys
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env.update(extra_env)
        code = "import database\n" + extra_code
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=backend_dir, env=env, capture_output=True, text=True, timeout=30,
        )

    def test_default_sslmode_is_prefer_when_unset(self):
        env = dict(os.environ)
        env.pop("DB_SSLMODE", None)
        result = self._import_database_with_env(
            {}, extra_code="print('SSLMODE=' + database.DB_SSLMODE)"
        )
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn("SSLMODE=prefer", result.stdout)

    def test_explicit_sslmode_env_var_is_honored(self):
        result = self._import_database_with_env(
            {"DB_SSLMODE": "verify-full"},
            extra_code="print('SSLMODE=' + database.DB_SSLMODE)",
        )
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn("SSLMODE=verify-full", result.stdout)

    def test_sslrootcert_omitted_from_connect_args_when_unset(self):
        result = self._import_database_with_env(
            {"DB_SSLROOTCERT": ""},
            extra_code="print('HAS_SSLROOTCERT=' + str('sslrootcert' in database._connect_args))",
        )
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn("HAS_SSLROOTCERT=False", result.stdout)

    def test_sslrootcert_included_when_set(self):
        # A fake, non-existent path is fine here -- this proves the
        # CONFIGURATION wiring (the key ends up in connect_args with the
        # right value), not that the file exists / a real TLS handshake
        # succeeds (that's covered by this phase's manual verification
        # against a disposable self-signed-CA Postgres container).
        fake_path = "C:\\fake\\path\\ca.pem" if os.name == "nt" else "/fake/path/ca.pem"
        result = self._import_database_with_env(
            {"DB_SSLROOTCERT": fake_path},
            extra_code="print('SSLROOTCERT=' + database._connect_args.get('sslrootcert', 'MISSING'))",
        )
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn(f"SSLROOTCERT={fake_path}", result.stdout)


if __name__ == "__main__":
    unittest.main()
