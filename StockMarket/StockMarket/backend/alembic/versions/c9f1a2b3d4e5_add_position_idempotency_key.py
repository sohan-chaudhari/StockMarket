"""add client_order_id idempotency key to positions

Revision ID: c9f1a2b3d4e5
Revises: a45e15c6abca
Create Date: 2026-10-07

Adds a nullable `positions.client_order_id` plus
UNIQUE(user_id, client_order_id) so a replayed order-submission request
(double-click / browser or network retry / duplicate frontend handler) can be
recognised and collapsed by the database into a single position, while
genuinely separate orders (different keys, including repeat entries in the same
stock) remain unaffected.

Why the schema and not an in-memory map: the requirement is ATOMIC rejection of
two concurrent identical requests. Only a database UNIQUE constraint can
arbitrate that across threads/processes and it also survives a restart. No
existing table/column offered a suitable unique key, so one small column plus a
unique index is the smallest correct mechanism.

Safety / footprint:
  * `client_order_id` is NULLABLE. Postgres (and SQLite) treat NULLs as distinct
    in a UNIQUE index, so every existing row (all NULL) and every request from a
    client that sends no key remain valid -- no backfill, no NOT NULL, no change
    to any existing behavior.
  * `positions` is tiny (tens of rows), so ADD COLUMN + CREATE UNIQUE INDEX are
    effectively instant metadata operations; the large `candles` table is not
    touched at all.

This migration is written to be idempotent (existence-checked) so it is also a
no-op against a database that already built `positions` from models.py (e.g. a
fresh `Base.metadata.create_all()` dev/CI database), matching the pattern used
by a45e15c6abca_reconcile_production_drift.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9f1a2b3d4e5'
down_revision: Union[str, Sequence[str], None] = 'a45e15c6abca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table, column):
    return conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :table AND column_name = :column
    """), {"table": table, "column": column}).scalar() is not None


def _constraint_exists(conn, table, name):
    return conn.execute(sa.text("""
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = :table AND constraint_name = :name
    """), {"table": table, "name": name}).scalar() is not None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    if not _column_exists(conn, "positions", "client_order_id"):
        op.add_column(
            "positions",
            sa.Column("client_order_id", sa.String(length=64), nullable=True),
        )
    if not _constraint_exists(conn, "positions", "uix_positions_user_client_order"):
        op.create_unique_constraint(
            "uix_positions_user_client_order",
            "positions",
            ["user_id", "client_order_id"],
        )


def downgrade() -> None:
    """Downgrade schema.

    Only ever run against a disposable test database. Dropping the key column
    removes the idempotency guarantee (replayed requests would again be able to
    create a second position) -- which is exactly the pre-migration state.
    """
    op.drop_constraint("uix_positions_user_client_order", "positions", type_="unique")
    op.drop_column("positions", "client_order_id")
