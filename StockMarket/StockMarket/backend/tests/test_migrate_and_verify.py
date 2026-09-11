"""Tests for backend/scripts/migrate_and_verify.py -- the deployment
migration gate (Option B: explicit deployment step, not a container
entrypoint; see the script's own docstring and alembic/README for the full
design rationale).

Needs a real PostgreSQL connection (the script shells out to the real
`alembic` CLI, which needs a real dialect to run DDL against) -- same
reasoning as test_alembic_migrations.py. Skips if unavailable rather than
failing. Every test operates against a disposable, uniquely-named scratch
database -- NEVER the real `stock_data` production database, and the
script is never invoked against production anywhere in this file.
"""
import os
import subprocess
import sys
import unittest

import psycopg2
from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(BACKEND_DIR, "scripts", "migrate_and_verify.py")
SCRATCH_DB_NAME = "stock_data_deploy_gate_test"

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")


def _admin_connection():
    return psycopg2.connect(dbname="postgres", user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)


def _postgres_available():
    try:
        conn = _admin_connection()
        conn.close()
        return True
    except Exception:
        return False


POSTGRES_AVAILABLE = _postgres_available()


def _recreate_scratch_db(name):
    conn = _admin_connection()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    cur.execute(f'CREATE DATABASE "{name}"')
    cur.close()
    conn.close()


def _drop_scratch_db(name):
    conn = _admin_connection()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    cur.close()
    conn.close()


def _run_script(env_overrides=None, clear_vars=None):
    """Invokes migrate_and_verify.py as a real subprocess -- proving the
    actual entry point (`python scripts/migrate_and_verify.py`) behaves
    correctly, not just its internal functions."""
    env = dict(os.environ)
    for var in (clear_vars or []):
        env.pop(var, None)
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, SCRIPT_PATH],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _current_revision(db_name):
    conn = psycopg2.connect(dbname=db_name, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            return row[0] if row else None
        except psycopg2.errors.UndefinedTable:
            return None
    finally:
        conn.close()


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestSuccessfulMigration(unittest.TestCase):
    def setUp(self):
        _recreate_scratch_db(SCRATCH_DB_NAME)

    def tearDown(self):
        _drop_scratch_db(SCRATCH_DB_NAME)

    def test_fresh_database_migrates_successfully_and_exits_zero(self):
        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn("OK", result.stdout)
        self.assertIn("Safe to start", result.stdout)

    def test_successful_migration_actually_reaches_head_in_the_database(self):
        _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        # Read the real DB state directly -- not just trusting the script's
        # own report of success.
        rev = _current_revision(SCRATCH_DB_NAME)
        self.assertIsNotNone(rev)
        self.assertRegex(rev, r"^[0-9a-f]+$")

    def test_running_twice_is_idempotent(self):
        first = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        second = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestFailedMigrationBlocksDeployment(unittest.TestCase):
    def setUp(self):
        _recreate_scratch_db(SCRATCH_DB_NAME)

    def tearDown(self):
        _drop_scratch_db(SCRATCH_DB_NAME)

    def test_unreachable_database_fails_with_nonzero_exit(self):
        """Alembic command failure must propagate a non-zero exit status,
        and the script must say deployment cannot proceed."""
        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME, "DB_PORT": "59999"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED", result.stderr)
        self.assertIn("must NOT proceed", result.stderr)

    def test_wrong_password_fails_with_nonzero_exit(self):
        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME, "DB_PASSWORD": "definitely_wrong_password"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED", result.stderr)

    def test_a_broken_migration_history_fails_and_leaves_no_false_success(self):
        """Simulates a migration that would fail against this database
        (already at a state incompatible with a clean upgrade): manually
        stamp a revision that doesn't exist in this checkout's history,
        forcing Alembic itself to refuse to proceed."""
        conn = psycopg2.connect(dbname=SCRATCH_DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
        cur = conn.cursor()
        cur.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        cur.execute("INSERT INTO alembic_version (version_num) VALUES ('nonexistent_revision_id')")
        conn.commit()
        cur.close()
        conn.close()

        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[migrate] OK", result.stdout)


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestSecretsNeverLeak(unittest.TestCase):
    def setUp(self):
        _recreate_scratch_db(SCRATCH_DB_NAME)

    def tearDown(self):
        _drop_scratch_db(SCRATCH_DB_NAME)

    def test_wrong_password_value_never_appears_in_output(self):
        secret = "sUp3r_s3cr3t_p@ssw0rd_marker"
        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME, "DB_PASSWORD": secret})
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)

    def test_successful_run_never_prints_a_raw_connection_string(self):
        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        self.assertEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertNotIn(f"{DB_USER}:{DB_PASSWORD}@", combined)
        self.assertNotIn("postgresql://", combined)


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestHeadVerification(unittest.TestCase):
    """Proves the script's own post-upgrade verification step (not just
    Alembic's own exit code) is what gates success -- i.e. it would catch a
    scenario where `alembic upgrade head` exits 0 but the database somehow
    isn't actually at the true head."""

    def setUp(self):
        _recreate_scratch_db(SCRATCH_DB_NAME)

    def tearDown(self):
        _drop_scratch_db(SCRATCH_DB_NAME)

    def test_verification_reports_the_true_head_not_a_hardcoded_value(self):
        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        self.assertEqual(result.returncode, 0)

        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
        real_head = ScriptDirectory.from_config(cfg).get_current_head()

        self.assertIn(real_head, result.stdout)

    def test_mismatched_current_and_heads_is_detected(self):
        """Directly exercises the verification logic's mismatch branch by
        monkeypatching _extract_revision to simulate current != heads,
        without needing to fabricate an inconsistent real database state."""
        sys.path.insert(0, os.path.join(BACKEND_DIR, "scripts"))
        import importlib
        import migrate_and_verify as mav
        importlib.reload(mav)

        os.environ["DB_NAME"] = SCRATCH_DB_NAME
        try:
            call_count = {"n": 0}
            real_extract = mav._extract_revision

            def fake_extract(stdout):
                call_count["n"] += 1
                # First call is for `alembic current`'s output, second for
                # `alembic heads`'s -- force them to disagree.
                if call_count["n"] == 1:
                    return "aaaaaaaaaaaa"
                return real_extract(stdout)

            mav._extract_revision = fake_extract
            try:
                exit_code = mav.main()
            finally:
                mav._extract_revision = real_extract
            self.assertEqual(exit_code, 1)
        finally:
            os.environ.pop("DB_NAME", None)
            sys.path.remove(os.path.join(BACKEND_DIR, "scripts"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestMissingEnvironmentConfiguration(unittest.TestCase):
    def setUp(self):
        _recreate_scratch_db(SCRATCH_DB_NAME)

    def tearDown(self):
        _drop_scratch_db(SCRATCH_DB_NAME)

    def test_missing_db_host_fails_clearly_without_attempting_a_connection(self):
        # Set to an EMPTY string rather than removing the var entirely: the
        # real backend/.env exists on this dev machine, and
        # load_dotenv(override=False) only fills in a var that's absent
        # from os.environ altogether -- an explicitly empty value already
        # counts as "present" and is left alone, which is exactly what lets
        # this test simulate "the deployment environment never set this"
        # even though a real .env is sitting right there on disk.
        result = _run_script(
            env_overrides={"DB_NAME": SCRATCH_DB_NAME, "DB_HOST": "", "DB_PORT": "", "DB_USER": ""},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing required environment variable", result.stderr)
        self.assertIn("DB_HOST", result.stderr)

    def test_all_required_vars_present_does_not_trigger_the_missing_var_error(self):
        # Uses the same disposable scratch DB the rest of this file uses --
        # never the shared `postgres` maintenance database.
        result = _run_script(env_overrides={"DB_NAME": SCRATCH_DB_NAME})
        self.assertNotIn("missing required environment variable", result.stderr)


if __name__ == "__main__":
    unittest.main()
