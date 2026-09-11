"""Tests for the Alembic migration baseline adopted in the DATABASE
MIGRATION CONTROL hardening phase.

Unlike the rest of this test suite, these tests need a REAL PostgreSQL
connection (not SQLite) -- Alembic's baseline migration contains
Postgres-specific DDL (a partial index with a WHERE clause, BigInteger,
etc.) that has no faithful SQLite equivalent, and the whole point is to
prove the migration works against the actual target dialect.

Every test operates against a disposable, uniquely-named scratch database
(never the real `stock_data`/production database, and never the dev
`stock_data_smoketest` database used by the Batch 2 smoke test) that is
created fresh in setUp and dropped in tearDown. If no local Postgres server
is reachable, these tests are skipped rather than failed, since that's an
environment-availability question, not a code-correctness one -- Batch 2
(CI) is what guarantees a Postgres server is actually present where these
tests run automatically.
"""
import os
import unittest

import psycopg2
from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH_DB_NAME = "stock_data_alembic_test"

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")


def _admin_connection():
    return psycopg2.connect(
        dbname="postgres", user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )


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


def _alembic_config(db_name):
    """Builds an Alembic Config pointing script_location at the real
    backend/alembic directory but targeting `db_name` -- mirrors how env.py
    resolves the URL (via database.SQLALCHEMY_DATABASE_URL, env-var driven),
    by temporarily overriding DB_NAME for the duration of the command."""
    from alembic.config import Config
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    return cfg


class _ScratchDbEnv:
    """Context manager: creates/drops a uniquely-named scratch DB and points
    DB_NAME at it for the duration, restoring the previous value after."""

    def __init__(self, name):
        self.name = name
        self._old_db_name = None

    def __enter__(self):
        _recreate_scratch_db(self.name)
        self._old_db_name = os.environ.get("DB_NAME")
        os.environ["DB_NAME"] = self.name
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._old_db_name is None:
            os.environ.pop("DB_NAME", None)
        else:
            os.environ["DB_NAME"] = self._old_db_name
        _drop_scratch_db(self.name)


def _query(db_name, sql, params=None):
    conn = psycopg2.connect(
        dbname=db_name, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestBaselineMigrationUpgrade(unittest.TestCase):
    def setUp(self):
        self.db_name = SCRATCH_DB_NAME
        self.env = _ScratchDbEnv(self.db_name)
        self.env.__enter__()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_upgrade_head_creates_all_application_tables(self):
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), "head")

        tables = {r[0] for r in _query(self.db_name, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
        for expected in ("candles", "users", "stock_metadata", "positions", "orders",
                         "transactions", "migration_jobs", "retention_jobs",
                         "verification_tokens", "token_blacklist"):
            self.assertIn(expected, tables)
        self.assertIn("alembic_version", tables)

        # The two unmanaged tables must never be created by Alembic itself.
        self.assertNotIn("candles_migration", tables)
        self.assertNotIn("candles_aug2021_repair_bak", tables)

    def test_upgrade_head_creates_expected_foreign_keys(self):
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), "head")

        fks = _query(self.db_name, """
            SELECT tc.table_name, kcu.column_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name AND tc.table_schema = rc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
        """)
        fk_set = {(r[0], r[1]) for r in fks}
        # These are the ones the pre-existing production DB is MISSING
        # (found during the Batch-1 audit) -- a fresh DB built from this
        # baseline must have all of them, proving the baseline reflects the
        # complete/correct schema, not production's drifted reality.
        for expected in [
            ("positions", "user_id"), ("orders", "position_id"), ("orders", "user_id"),
            ("transactions", "user_id"), ("transactions", "position_id"),
            ("verification_tokens", "user_id"),
        ]:
            self.assertIn(expected, fk_set, f"missing FK {expected} in fresh baseline")
        self.assertEqual(len(fks), 8)

    def test_upgrade_head_creates_the_four_formalized_v007_indexes(self):
        """These 4 indexes previously existed ONLY as raw SQL in main.py's
        startup ("v007"), invisible to any ORM/Alembic tooling. Formalized
        as real Index() declarations in models.py (ALEMBIC-01) -- this
        proves they now come from the migration, not a startup side effect."""
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), "head")

        for table, index_name in [
            ("candles", "ix_candle_ticker_tf_ts"),
            ("current_day_candle", "ix_currentdaycandle_ticker_date"),
            ("stock_metadata", "ix_metadata_is_premium"),
            ("stock_data", "ix_stock_data_ticker_date_desc"),
        ]:
            idx = _query(self.db_name, "SELECT indexname FROM pg_indexes WHERE tablename=%s AND indexname=%s",
                         (table, index_name))
            self.assertEqual(len(idx), 1, f"expected index {index_name} on {table}")

    def test_upgrade_head_creates_candle_uniqueness_constraint(self):
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), "head")

        cons = _query(self.db_name, """
            SELECT indexdef FROM pg_indexes WHERE tablename='candles' AND indexname='uix_candle_key'
        """)
        self.assertEqual(len(cons), 1)
        self.assertIn("ticker", cons[0][0])
        self.assertIn("timeframe", cons[0][0])
        self.assertIn("timestamp", cons[0][0])

    def test_upgrade_head_is_idempotent(self):
        """Running upgrade head twice must not error -- Alembic checks the
        DB's current revision and no-ops if already there."""
        from alembic import command
        cfg = _alembic_config(self.db_name)
        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")  # must not raise

        tables = _query(self.db_name, "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        self.assertEqual(tables[0][0], 26)  # 25 app tables + alembic_version, not doubled


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestBaselineMigrationDowngrade(unittest.TestCase):
    """Downgrade is tested ONLY against disposable scratch databases -- per
    the project's safety rules, a destructive downgrade must never run
    against production."""

    def setUp(self):
        self.db_name = SCRATCH_DB_NAME
        self.env = _ScratchDbEnv(self.db_name)
        self.env.__enter__()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_downgrade_base_removes_all_application_tables(self):
        from alembic import command
        cfg = _alembic_config(self.db_name)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")

        tables = {r[0] for r in _query(self.db_name, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
        # alembic_version itself is Alembic's own bookkeeping table and is
        # never dropped by downgrade -- everything application-owned must be gone.
        self.assertEqual(tables, {"alembic_version"})

    def test_downgrade_then_upgrade_round_trip_is_clean(self):
        from alembic import command
        cfg = _alembic_config(self.db_name)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")  # must succeed again from a torn-down state

        tables = _query(self.db_name, "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        self.assertEqual(tables[0][0], 26)


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestUnmanagedTablesAreNeverTouched(unittest.TestCase):
    """Proves the include_object filter in env.py actually works: a table
    matching one of the two known unmanaged names must survive both
    `upgrade head` and `downgrade base` completely untouched."""

    def setUp(self):
        self.db_name = SCRATCH_DB_NAME
        self.env = _ScratchDbEnv(self.db_name)
        self.env.__enter__()
        # Simulate the real production shape: candles_migration already
        # exists (owned by the migration/ tool), created BEFORE Alembic
        # ever runs against this database.
        conn = psycopg2.connect(dbname=self.db_name, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
        cur = conn.cursor()
        cur.execute("CREATE TABLE candles_migration (id serial PRIMARY KEY, note text)")
        cur.execute("INSERT INTO candles_migration (note) VALUES ('pre-existing data')")
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_unmanaged_table_survives_upgrade_and_downgrade(self):
        from alembic import command
        cfg = _alembic_config(self.db_name)

        command.upgrade(cfg, "head")
        rows = _query(self.db_name, "SELECT note FROM candles_migration")
        self.assertEqual(rows, [("pre-existing data",)])

        command.downgrade(cfg, "base")
        rows = _query(self.db_name, "SELECT note FROM candles_migration")
        self.assertEqual(rows, [("pre-existing data",)], "downgrade must never touch an unmanaged table")


if __name__ == "__main__":
    unittest.main()
