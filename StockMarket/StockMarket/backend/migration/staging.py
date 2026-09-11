"""
Staging-table safety for the scoped 1D migration entrypoint.
======================================================================
Answers, read-only: is it safe to create/reuse `candles_migration`, or does
it contain stale/incompatible data from a different run that must not be
silently absorbed into this one? Creation only happens in
prepare_staging_table(), and only when the caller has already passed the
--execute-1d-migration gate -- never during --dry-run.
"""

from enum import Enum


class StagingStatus(str, Enum):
    ABSENT = "absent"                   # doesn't exist -- safe to create fresh
    COMPATIBLE_EMPTY = "compatible_empty"       # exists, right schema, no rows -- safe to reuse
    COMPATIBLE_1D_ONLY = "compatible_1d_only"   # exists, right schema, only 1D rows -- safe to reuse (likely a resumed run)
    INCOMPATIBLE = "incompatible"       # wrong schema, or contains non-1D rows -- refuse


def staging_table_status(db, staging_table: str = "candles_migration") -> StagingStatus:
    """Read-only. Never creates, drops, or modifies anything.

    Uses SQLAlchemy's dialect-agnostic Inspector (not raw `information_schema`
    SQL, which is Postgres-specific and doesn't exist in SQLite) -- the exact
    same code path this function uses in production Postgres is what the
    test suite exercises against an in-memory SQLite database."""
    from sqlalchemy import text, inspect

    inspector = inspect(db.get_bind())
    if not inspector.has_table(staging_table):
        return StagingStatus.ABSENT

    candles_cols = {c["name"] for c in inspector.get_columns("candles")}
    staging_cols = {c["name"] for c in inspector.get_columns(staging_table)}
    if not candles_cols.issubset(staging_cols):
        return StagingStatus.INCOMPATIBLE

    row_count = db.execute(text(f"SELECT COUNT(*) FROM {staging_table}")).scalar()
    if row_count == 0:
        return StagingStatus.COMPATIBLE_EMPTY

    other_timeframe_rows = db.execute(text(
        f"SELECT COUNT(*) FROM {staging_table} WHERE timeframe != '1D'"
    )).scalar()
    if other_timeframe_rows > 0:
        # Stale data from a non-1D-only run (e.g. a prior full cmd_migrate
        # attempt) -- must not silently become part of this run.
        return StagingStatus.INCOMPATIBLE

    return StagingStatus.COMPATIBLE_1D_ONLY


def prepare_staging_table(db, staging_table: str = "candles_migration") -> str:
    """Only call this from the authorized (--execute-1d-migration) path,
    never from dry-run. Returns 'created' or 'reused'; raises RuntimeError
    (refusing to proceed) if the table exists but is incompatible -- the
    caller must resolve that by hand (e.g. DROP TABLE), not have it done
    silently on their behalf.

    Creation copies `candles`'s columns, constraints, and indexes via
    Table.to_metadata() -- including the unique (ticker, timeframe,
    timestamp) constraint that BatchDownloader._bulk_insert's
    `ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING` requires to
    exist on the target table -- using SQLAlchemy Core (dialect-agnostic:
    the same code path this module's SQLite-based tests exercise runs
    identically against production Postgres).

    to_metadata() preserves the SOURCE table's index/constraint names
    verbatim (e.g. `idx_candle_lookup`, `uix_candle_key`), which would
    collide with the already-existing indexes/constraints of the same name
    on `candles` itself -- both Postgres and SQLite require unique
    index/constraint names within a schema. Every copied index and named
    constraint is explicitly renamed (suffixed with the staging table name)
    before creation to avoid that collision."""
    from sqlalchemy import MetaData

    status = staging_table_status(db, staging_table)
    if status == StagingStatus.ABSENT:
        from models import Candle
        meta = MetaData()
        staging = Candle.__table__.to_metadata(meta, name=staging_table)
        for idx in staging.indexes:
            idx.name = f"{idx.name}_{staging_table}"
        for constraint in staging.constraints:
            if constraint.name:
                constraint.name = f"{constraint.name}_{staging_table}"
        staging.create(bind=db.get_bind())
        return "created"
    if status in (StagingStatus.COMPATIBLE_EMPTY, StagingStatus.COMPATIBLE_1D_ONLY):
        return "reused"
    raise RuntimeError(
        f"Staging table '{staging_table}' exists but is incompatible with a 1D-only run "
        f"(wrong schema, or contains non-1D rows from a different migration attempt). "
        f"Refusing to reuse it automatically -- inspect it and, if appropriate, "
        f"`DROP TABLE {staging_table}` by hand before retrying."
    )
