"""Tests for alembic/versions/a45e15c6abca_reconcile_production_drift.py --
the migration that closes the gap found by the PRODUCTION SCHEMA DRIFT
RESOLUTION audit between the live production database and the Alembic
baseline (0e36d70f68ce).

Needs a real PostgreSQL connection (same reasoning as test_alembic_
migrations.py: the DDL here -- FK/unique-constraint drop+recreate, VARCHAR
widening -- has no faithful SQLite equivalent). Skips if unavailable rather
than failing, matching test_alembic_migrations.py's pattern exactly.

Every test operates against a disposable, uniquely-named scratch database --
NEVER the real `stock_data` production database.
"""
import os
import unittest

import psycopg2
from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_REVISION = "0e36d70f68ce"
DRIFT_FIX_REVISION = "a45e15c6abca"

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


def _alembic_config(db_name):
    from alembic.config import Config
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    return cfg


class _ScratchDbEnv:
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


def _connect(db_name):
    return psycopg2.connect(dbname=db_name, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)


def _query(db_name, sql, params=None):
    conn = _connect(db_name)
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def _execute(db_name, sql, params=None, many=None):
    conn = _connect(db_name)
    try:
        cur = conn.cursor()
        if many:
            cur.executemany(sql, many)
        else:
            cur.execute(sql, params or ())
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _simulate_drifted_production_schema(db_name):
    """Takes a DB already at the baseline revision (full correct schema) and
    "un-fixes" it back to exactly what the real production audit found --
    the same 6 missing FKs, the same 2 additional issues found during the
    audit, giving a faithful drifted starting point to test the
    reconciliation migration against."""
    _execute(db_name, """
        ALTER TABLE positions DROP CONSTRAINT positions_user_id_fkey;
        ALTER TABLE orders DROP CONSTRAINT orders_position_id_fkey;
        ALTER TABLE orders DROP CONSTRAINT orders_user_id_fkey;
        ALTER TABLE transactions DROP CONSTRAINT transactions_user_id_fkey;
        ALTER TABLE transactions DROP CONSTRAINT transactions_position_id_fkey;
        ALTER TABLE verification_tokens DROP CONSTRAINT verification_tokens_user_id_fkey;

        ALTER TABLE user_watchlist DROP CONSTRAINT user_watchlist_user_id_fkey;
        ALTER TABLE user_watchlist ADD CONSTRAINT user_watchlist_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES users(user_id);

        ALTER TABLE user_chart_settings DROP CONSTRAINT uix_user_chart_settings;
        ALTER TABLE user_chart_settings ADD CONSTRAINT user_chart_settings_user_id_key UNIQUE (user_id);

        ALTER TABLE orders ALTER COLUMN order_type TYPE VARCHAR(10);

        ALTER TABLE stock_metadata ALTER COLUMN is_active DROP NOT NULL;
        ALTER TABLE stock_metadata ALTER COLUMN is_premium DROP NOT NULL;
    """)


def _fk_exists(db_name, table, column):
    rows = _query(db_name, """
        SELECT rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.referential_constraints rc
          ON tc.constraint_name = rc.constraint_name AND tc.table_schema = rc.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = %s AND kcu.column_name = %s
    """, (table, column))
    return rows[0][0] if rows else None


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestCleanSchemaMigration(unittest.TestCase):
    """Step 4.1: clean schema -> migration (baseline + drift-fix from empty)."""

    def setUp(self):
        self.db_name = "stock_data_drift_test_clean"
        self.env = _ScratchDbEnv(self.db_name)
        self.env.__enter__()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_upgrade_head_from_empty_applies_both_migrations(self):
        from alembic import command
        cfg = _alembic_config(self.db_name)
        command.upgrade(cfg, "head")

        rev = _query(self.db_name, "SELECT version_num FROM alembic_version")
        self.assertEqual(rev[0][0], DRIFT_FIX_REVISION)

        # All 6 FKs present with the correct ondelete rule.
        self.assertEqual(_fk_exists(self.db_name, "positions", "user_id"), "CASCADE")
        self.assertEqual(_fk_exists(self.db_name, "orders", "position_id"), "CASCADE")
        self.assertEqual(_fk_exists(self.db_name, "orders", "user_id"), "CASCADE")
        self.assertEqual(_fk_exists(self.db_name, "transactions", "user_id"), "CASCADE")
        self.assertEqual(_fk_exists(self.db_name, "transactions", "position_id"), "SET NULL")
        self.assertEqual(_fk_exists(self.db_name, "verification_tokens", "user_id"), "CASCADE")
        self.assertEqual(_fk_exists(self.db_name, "user_watchlist", "user_id"), "CASCADE")

        # user_chart_settings unique constraint is (user_id, ticker).
        cons = _query(self.db_name, """
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'user_chart_settings'::regclass AND contype = 'u'
        """)
        self.assertIn("user_id", cons[0][0])
        self.assertIn("ticker", cons[0][0])

        # orders.order_type is VARCHAR(20).
        width = _query(self.db_name, """
            SELECT character_maximum_length FROM information_schema.columns
            WHERE table_name='orders' AND column_name='order_type'
        """)
        self.assertEqual(width[0][0], 20)

        # stock_metadata.is_active/is_premium are NOT NULL.
        nullability = _query(self.db_name, """
            SELECT column_name, is_nullable FROM information_schema.columns
            WHERE table_name='stock_metadata' AND column_name IN ('is_active', 'is_premium')
        """)
        for _col, is_nullable in nullability:
            self.assertEqual(is_nullable, "NO")


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestDriftedProductionLikeSchemaMigration(unittest.TestCase):
    """Step 4.2/4.3: simulated production-like schema WITH the known drift,
    holding existing valid data -> migration must reconcile it and leave the
    data intact."""

    def setUp(self):
        self.db_name = "stock_data_drift_test_prodlike"
        self.env = _ScratchDbEnv(self.db_name)
        self.env.__enter__()
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), BASELINE_REVISION)
        _simulate_drifted_production_schema(self.db_name)

        # Seed valid, FK-consistent data -- exactly the shape production has
        # (small numbers of real rows across these tables).
        _execute(self.db_name, """
            INSERT INTO users (user_id, email, password_hash) VALUES (1, 'a@x.com', 'h');
            INSERT INTO positions (id, user_id, ticker, position_type, quantity, entry_price, total_investment)
                VALUES (1, 1, 'RELIANCE', 'LONG', 10, 100.0, 1000.0);
            INSERT INTO orders (id, position_id, user_id, ticker, order_type, trigger_price)
                VALUES (1, 1, 1, 'RELIANCE', 'TP', 110.0);
            INSERT INTO transactions (id, user_id, position_id, transaction_type, amount, balance_after)
                VALUES (1, 1, 1, 'OPEN', -1000.0, 99000.0);
            INSERT INTO verification_tokens (id, user_id, token_hash, token_type, expires_at)
                VALUES (1, 1, 'tok123', 'email_verification', now() + interval '1 day');
            INSERT INTO user_watchlist (id, user_id, ticker) VALUES (1, 1, 'TCS');
        """)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_migration_reconciles_drift_and_preserves_data(self):
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), "head")

        # Reconciliation applied.
        self.assertEqual(_fk_exists(self.db_name, "positions", "user_id"), "CASCADE")
        self.assertEqual(_fk_exists(self.db_name, "user_watchlist", "user_id"), "CASCADE")
        width = _query(self.db_name, """
            SELECT character_maximum_length FROM information_schema.columns
            WHERE table_name='orders' AND column_name='order_type'
        """)
        self.assertEqual(width[0][0], 20)

        # Every seeded row survived untouched.
        self.assertEqual(_query(self.db_name, "SELECT email FROM users WHERE user_id=1")[0][0], "a@x.com")
        self.assertEqual(_query(self.db_name, "SELECT ticker FROM positions WHERE id=1")[0][0], "RELIANCE")
        self.assertEqual(_query(self.db_name, "SELECT order_type FROM orders WHERE id=1")[0][0], "TP")
        self.assertEqual(_query(self.db_name, "SELECT amount FROM transactions WHERE id=1")[0][0], -1000.0)
        self.assertEqual(_query(self.db_name, "SELECT ticker FROM user_watchlist WHERE id=1")[0][0], "TCS")

    def test_cascade_delete_of_user_removes_dependent_rows(self):
        """Proves the new ON DELETE CASCADE is not just present in the
        catalog but functionally correct: deleting a user now cascades to
        their positions/orders/verification_tokens/watchlist -- AND to their
        transactions (transactions.user_id is ALSO ondelete=CASCADE per
        models.py), so the whole per-user footprint is removed together."""
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), "head")

        _execute(self.db_name, "DELETE FROM users WHERE user_id = 1")

        self.assertEqual(_query(self.db_name, "SELECT count(*) FROM positions")[0][0], 0)
        self.assertEqual(_query(self.db_name, "SELECT count(*) FROM orders")[0][0], 0)
        self.assertEqual(_query(self.db_name, "SELECT count(*) FROM verification_tokens")[0][0], 0)
        self.assertEqual(_query(self.db_name, "SELECT count(*) FROM user_watchlist")[0][0], 0)
        self.assertEqual(_query(self.db_name, "SELECT count(*) FROM transactions")[0][0], 0)

    def test_position_delete_sets_transaction_position_id_null(self):
        """Exercises transactions.position_id's ON DELETE SET NULL rule in
        isolation from transactions.user_id's CASCADE: delete the POSITION
        directly (user still exists) and confirm the transaction row
        survives with position_id nulled out, not deleted -- the ledger
        entry itself is meant to be immutable per its own docstring, even
        though it does NOT survive its owning user being deleted (see the
        test above; that's transactions.user_id's CASCADE, a separate FK)."""
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), "head")

        _execute(self.db_name, "DELETE FROM positions WHERE id = 1")

        remaining = _query(self.db_name, "SELECT position_id FROM transactions WHERE id=1")
        self.assertEqual(len(remaining), 1, "the transaction ledger row itself must survive")
        self.assertIsNone(remaining[0][0], "position_id must be nulled out, not left dangling")

    def test_migration_is_idempotent(self):
        from alembic import command
        cfg = _alembic_config(self.db_name)
        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")  # must not raise
        self.assertEqual(_fk_exists(self.db_name, "positions", "user_id"), "CASCADE")

    def test_downgrade_reverts_to_drifted_shape(self):
        from alembic import command
        cfg = _alembic_config(self.db_name)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, BASELINE_REVISION)

        # All 6 previously-missing FKs gone again.
        self.assertIsNone(_fk_exists(self.db_name, "positions", "user_id"))
        self.assertIsNone(_fk_exists(self.db_name, "transactions", "position_id"))
        # user_watchlist's FK still exists (it was never missing, only its
        # ondelete rule was wrong) -- downgrade reverts to no-ondelete
        # ("NO ACTION" is Postgres's default when none is specified),
        # matching the original drifted production shape exactly.
        self.assertEqual(_fk_exists(self.db_name, "user_watchlist", "user_id"), "NO ACTION")
        # order_type back to VARCHAR(10).
        width = _query(self.db_name, """
            SELECT character_maximum_length FROM information_schema.columns
            WHERE table_name='orders' AND column_name='order_type'
        """)
        self.assertEqual(width[0][0], 10)
        # Data itself is untouched by the downgrade.
        self.assertEqual(_query(self.db_name, "SELECT ticker FROM positions WHERE id=1")[0][0], "RELIANCE")


@unittest.skipUnless(POSTGRES_AVAILABLE, "no local PostgreSQL server reachable")
class TestOrphanedDataFailsLoudly(unittest.TestCase):
    """Step 4.4: if orphaned data existed (it doesn't in real production,
    per the audit), adding the FK must fail LOUDLY, never silently drop or
    rewrite the offending rows."""

    def setUp(self):
        self.db_name = "stock_data_drift_test_orphan"
        self.env = _ScratchDbEnv(self.db_name)
        self.env.__enter__()
        from alembic import command
        command.upgrade(_alembic_config(self.db_name), BASELINE_REVISION)
        _simulate_drifted_production_schema(self.db_name)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_orphaned_position_blocks_the_migration(self):
        # A position referencing a user_id that does not exist -- exactly
        # the scenario the audit confirmed does NOT exist in real production
        # (0 orphans), simulated here to prove the failure mode is safe.
        _execute(self.db_name, """
            INSERT INTO positions (id, user_id, ticker, position_type, quantity, entry_price, total_investment)
            VALUES (1, 9999, 'ORPHANED', 'LONG', 1, 1.0, 1.0)
        """)

        from alembic import command
        with self.assertRaises(Exception) as ctx:
            command.upgrade(_alembic_config(self.db_name), "head")
        # A real FK-violation error, not a silent pass.
        self.assertIn("foreign key", str(ctx.exception).lower())

        # The orphaned row must still be there afterward, untouched --
        # a failed migration must not have deleted or rewritten it.
        remaining = _query(self.db_name, "SELECT ticker FROM positions WHERE id=1")
        self.assertEqual(remaining[0][0], "ORPHANED")

        # The whole migration is one transaction -- a failure on ANY step
        # must roll back ALL of it, not leave a partially-applied schema.
        # Confirm a later, independent change (order_type widening) also
        # did not take effect.
        width = _query(self.db_name, """
            SELECT character_maximum_length FROM information_schema.columns
            WHERE table_name='orders' AND column_name='order_type'
        """)
        self.assertEqual(width[0][0], 10, "a failed migration must roll back atomically, not partially apply")


if __name__ == "__main__":
    unittest.main()
