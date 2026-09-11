"""reconcile_production_drift

Revision ID: a45e15c6abca
Revises: 0e36d70f68ce
Create Date: 2026-09-06 21:43:06.651770

Reconciles the live production schema with the Alembic baseline
(0e36d70f68ce), found via a read-only audit (see alembic/README) run BEFORE
this migration was written -- every change below was verified safe against
actual production data first:

  1. Six foreign keys that models.py has always declared but production
     never had (create_all() never alters an already-existing table):
       positions.user_id -> users.user_id                   ON DELETE CASCADE
       orders.position_id -> positions.id                   ON DELETE CASCADE
       orders.user_id -> users.user_id                       ON DELETE CASCADE
       transactions.user_id -> users.user_id                 ON DELETE CASCADE
       transactions.position_id -> positions.id              ON DELETE SET NULL
       verification_tokens.user_id -> users.user_id          ON DELETE CASCADE
     Audited: 0 NULL values and 0 orphaned rows across all six relationships
     (positions=17, orders=20, transactions=31, verification_tokens=1 rows
     total at audit time) -- adding these constraints cannot fail. No
     application code currently deletes a `users` or `positions` row (no
     such endpoint exists), so CASCADE introduces a safety net for a path
     nothing exercises yet rather than changing any current behavior.

  2. user_watchlist's existing FK is missing ON DELETE CASCADE (found during
     this audit, not in the original known-drift list) -- the constraint
     itself already exists, only its delete rule needs replacing.

  3. user_chart_settings has UNIQUE(user_id) in production but models.py
     declares UNIQUE(user_id, ticker) (found during this audit) -- production
     literally cannot store more than one ticker's chart settings per user
     right now. Table has ZERO rows in production, so this is a pure,
     zero-risk correction, not a data migration.

  4. orders.order_type is VARCHAR(10) in production, models.py wants
     VARCHAR(20) (found during this audit). All 20 existing rows are 'TP' or
     'SL' (2 chars) -- pure widening, Postgres does not rewrite the table for
     this, no truncation risk.

  5. stock_metadata.is_active/is_premium: production has always enforced
     these NOT NULL (the old v005 startup migration created them that way);
     models.py never declared it. Fixed in models.py itself (this migration
     just makes it explicit/enforced for any environment that built its
     schema from the ORIGINAL baseline migration with the looser nullable
     default) -- confirmed 0 NULL rows in production before this change.

IMPORTANT -- why every operation below is a runtime existence/state check
before acting, not an unconditional DDL statement: the baseline migration
(0e36d70f68ce) ALREADY creates all 6 FKs correctly (models.py has always
declared them -- the baseline reflects models.py, not production). A truly
FRESH database (a new dev setup, CI, `alembic upgrade head` from empty) runs
the baseline first and already has the correct schema by the time this
migration starts -- an unconditional `op.create_foreign_key(...)` here would
then fail with "constraint already exists". Production, on the other hand,
gets onboarded via `alembic stamp 0e36d70f68ce` (no DDL executed) followed by
a REAL `alembic upgrade head` that only ever runs this migration's DDL
against an actually-drifted database. Checking current state first makes
this single migration file correct for BOTH paths: a genuine no-op against
an already-correct fresh database, and a real fix against drifted
production. Confirmed necessary by testing against a from-empty database in
Step 4 of this phase's testing -- an earlier, unconditional version of this
migration failed exactly this way.

Deliberately NOT included (see the audit report for full rationale):
  - ix_candles_ticker / ix_candles_id: both are redundant with existing
    composite-index prefixes (uix_candle_key / ix_candle_ticker_tf_ts already
    lead with `ticker`; ix_candles_id would duplicate the primary key's own
    unique index). Every real query in this codebase that filters `candles`
    by ticker also filters by timeframe in the same call (verified via
    repo-wide grep) -- a dedicated single-column index would add real,
    ongoing write cost to the busiest table in the system (17.6M+ rows,
    5.7GB) for no measured read benefit. Flagged for a deliberate decision,
    not applied by default.
  - The 5 deprecated_intraday_* tables' index/PK naming drift (their indexes
    carry pre-rename names, e.g. ix_intraday_candles_15min_id instead of
    ix_deprecated_intraday_candles_15min_id) -- these tables are explicitly
    frozen/out-of-scope; see alembic/env.py's _UNMANAGED_TABLES, which now
    excludes all five so Alembic never proposes touching them at all.
  - candles_migration / candles_aug2021_repair_bak: unmanaged, as before.

All six tables touched by this migration are tiny (0-31 rows each) --
every operation here takes an ACCESS EXCLUSIVE lock only for the
sub-millisecond duration of a metadata-only change. The one large table in
the system (candles, 17.6M rows) is not touched by this migration at all.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a45e15c6abca'
down_revision: Union[str, Sequence[str], None] = '0e36d70f68ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk_state(conn, table, column):
    """Returns the delete_rule of the FK on table.column, or None if no FK
    exists on that column at all."""
    row = conn.execute(sa.text("""
        SELECT tc.constraint_name, rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.referential_constraints rc
          ON tc.constraint_name = rc.constraint_name AND tc.table_schema = rc.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = :table AND kcu.column_name = :column
    """), {"table": table, "column": column}).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _ensure_fk(conn, constraint_name, table, ref_table, column, ref_column, ondelete):
    """Idempotent: leaves an already-correct FK alone (the fresh-database
    path, where the baseline already created it correctly), fixes one with
    the wrong ondelete rule (the user_watchlist case), or creates one that's
    missing entirely (the production case for the other 6)."""
    existing_name, existing_rule = _fk_state(conn, table, column)
    if existing_rule == ondelete:
        return  # already exactly right -- nothing to do
    if existing_name is not None:
        op.drop_constraint(existing_name, table, type_="foreignkey")
    op.create_foreign_key(
        constraint_name, table, ref_table, [column], [ref_column], ondelete=ondelete
    )


def _unique_exists(conn, table, columns):
    rows = conn.execute(sa.text("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'UNIQUE' AND tc.table_name = :table
        ORDER BY kcu.ordinal_position
    """), {"table": table}).fetchall()
    # Group columns per constraint isn't distinguishable from this flat
    # query alone across multiple unique constraints on the same table, but
    # this table only ever has one unique constraint at a time in either
    # its drifted or fixed shape, so a simple set comparison is sufficient.
    return {r[0] for r in rows} == set(columns)


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    # 1. Six missing foreign keys -- all pre-verified zero-orphan, zero-null.
    #    Idempotent: a no-op if the baseline already created these correctly
    #    (the fresh-database path).
    _ensure_fk(conn, "positions_user_id_fkey", "positions", "users", "user_id", "user_id", "CASCADE")
    _ensure_fk(conn, "orders_position_id_fkey", "orders", "positions", "position_id", "id", "CASCADE")
    _ensure_fk(conn, "orders_user_id_fkey", "orders", "users", "user_id", "user_id", "CASCADE")
    _ensure_fk(conn, "transactions_user_id_fkey", "transactions", "users", "user_id", "user_id", "CASCADE")
    _ensure_fk(conn, "transactions_position_id_fkey", "transactions", "positions", "position_id", "id", "SET NULL")
    _ensure_fk(conn, "verification_tokens_user_id_fkey", "verification_tokens", "users", "user_id", "user_id", "CASCADE")

    # 2. user_watchlist: existing FK missing ON DELETE CASCADE in production;
    #    already correct on a fresh database -- _ensure_fk no-ops there.
    _ensure_fk(conn, "user_watchlist_user_id_fkey", "user_watchlist", "users", "user_id", "user_id", "CASCADE")

    # 3. user_chart_settings: UNIQUE(user_id) -> UNIQUE(user_id, ticker) in
    #    production. 0 rows in production at audit time -- cannot violate
    #    the new, stricter (in permissiveness) constraint. No-op on a fresh
    #    database, which already has the (user_id, ticker) constraint.
    if not _unique_exists(conn, "user_chart_settings", ["user_id", "ticker"]):
        existing = conn.execute(sa.text("""
            SELECT tc.constraint_name FROM information_schema.table_constraints tc
            WHERE tc.constraint_type = 'UNIQUE' AND tc.table_name = 'user_chart_settings'
        """)).fetchone()
        if existing is not None:
            op.drop_constraint(existing[0], "user_chart_settings", type_="unique")
        op.create_unique_constraint(
            "uix_user_chart_settings", "user_chart_settings", ["user_id", "ticker"]
        )

    # 4. orders.order_type: widen VARCHAR(10) -> VARCHAR(20) in production.
    #    Widening only; Postgres does not rewrite the table for this.
    #    No-op (via existing_type check) on a fresh database already at 20.
    width = conn.execute(sa.text("""
        SELECT character_maximum_length FROM information_schema.columns
        WHERE table_name='orders' AND column_name='order_type'
    """)).scalar()
    if width != 20:
        op.alter_column(
            "orders", "order_type",
            existing_type=sa.String(length=width),
            type_=sa.String(length=20),
        )

    # 5. stock_metadata.is_active/is_premium: enforce NOT NULL explicitly in
    #    production (already zero NULLs there). No-op on a fresh database,
    #    which now (per the models.py DRIFT-01 fix) already declares this.
    for col in ("is_active", "is_premium"):
        nullable = conn.execute(sa.text("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name='stock_metadata' AND column_name=:col
        """), {"col": col}).scalar()
        if nullable == "YES":
            op.alter_column("stock_metadata", col, existing_type=sa.Boolean(), nullable=False)


def downgrade() -> None:
    """Downgrade schema.

    Only ever run against a disposable test database -- never production
    (see this project's safety rules). Reverts unconditionally back to the
    exact drifted shape (this migration's whole purpose is to leave a
    database in the KNOWN, single "reconciled" state; downgrade's job is
    just to undo that, not to re-detect which state it started from).
    Narrowing orders.order_type back to VARCHAR(10) will raise (not silently
    truncate) if any row longer than 10 chars was written after upgrade --
    a loud failure, not data corruption, but still something to be aware of
    before downgrading a database that's seen real post-upgrade writes.
    """
    op.alter_column("stock_metadata", "is_premium", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("stock_metadata", "is_active", existing_type=sa.Boolean(), nullable=True)

    op.alter_column(
        "orders", "order_type",
        existing_type=sa.String(length=20),
        type_=sa.String(length=10),
    )

    op.drop_constraint("uix_user_chart_settings", "user_chart_settings", type_="unique")
    op.create_unique_constraint(
        "user_chart_settings_user_id_key", "user_chart_settings", ["user_id"]
    )

    op.drop_constraint("user_watchlist_user_id_fkey", "user_watchlist", type_="foreignkey")
    op.create_foreign_key(
        "user_watchlist_user_id_fkey", "user_watchlist", "users",
        ["user_id"], ["user_id"],
    )

    op.drop_constraint("verification_tokens_user_id_fkey", "verification_tokens", type_="foreignkey")
    op.drop_constraint("transactions_position_id_fkey", "transactions", type_="foreignkey")
    op.drop_constraint("transactions_user_id_fkey", "transactions", type_="foreignkey")
    op.drop_constraint("orders_user_id_fkey", "orders", type_="foreignkey")
    op.drop_constraint("orders_position_id_fkey", "orders", type_="foreignkey")
    op.drop_constraint("positions_user_id_fkey", "positions", type_="foreignkey")
