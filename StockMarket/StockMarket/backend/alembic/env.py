import os
import sys
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy import pool

from alembic import context

# Ensure backend/ is importable regardless of the cwd Alembic is invoked
# from (mirrors conftest.py's sys.path handling for the test suite).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load backend/.env into os.environ, exactly like every other module in
# this app (database.py, auth.py, angelone_service.py) already does --
# env.py previously read os.getenv(...) directly with no loading step of
# its own, which worked fine whenever DB_* vars happened to already be in
# the shell's real environment, but produced a bare
# "fe_sendauth: no password supplied" failure the first time `alembic` was
# invoked from a shell that had never sourced .env (found while running the
# real production reconciliation -- DB_PASSWORD was empty in that shell
# even though other, unrelated commands in the same session had it).
# override=False (the default) means an already-set real env var still
# wins, matching database.py's own behavior.
load_dotenv()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build the DB URL from the SAME environment variables database.py uses --
# never hardcoded in alembic.ini, so no credentials of any kind live in a
# tracked file.
#
# Deliberately NOT simply `import database; DATABASE_URL =
# database.SQLALCHEMY_DATABASE_URL`: that constant is computed ONCE, the
# first time database.py is imported by ANYTHING in the process, and Python
# caches the module in sys.modules from then on -- re-importing it here
# returns the same cached module with the same frozen URL, even if DB_NAME
# (or any other DB_* var) was changed afterward. This bit the migration test
# suite directly: tests that override DB_NAME to point at a scratch database
# passed in isolation but failed when run after any other test file had
# already imported database.py against the real DB_NAME. Rebuilding the URL
# from os.environ fresh, every time env.py runs (which Alembic does on every
# command, via run_env() -> load_python_file()), avoids that staleness
# entirely. This intentionally duplicates database.py's small amount of URL
# construction logic -- the alternative (importlib.reload(database)) risks
# side effects on database.engine that other already-imported modules hold
# live references to.
#
# Also deliberately NOT routed through config.set_main_option()/
# engine_from_config (the cookiecutter default): ConfigParser applies
# %-interpolation to every value it stores, and a URL-encoded password (e.g.
# "%40" for "@") is indistinguishable from interpolation syntax to it,
# raising "invalid interpolation syntax". create_engine() sidesteps
# ConfigParser entirely for this value.
import urllib.parse  # noqa: E402

_db_user = os.getenv("DB_USER", "postgres")
_db_password = os.getenv("DB_PASSWORD", "")
_db_host = os.getenv("DB_HOST", "localhost")
_db_port = os.getenv("DB_PORT", "5432")
_db_name = os.getenv("DB_NAME", "stock_data")
_safe_user = urllib.parse.quote_plus(_db_user)
if _db_password:
    DATABASE_URL = f"postgresql://{_safe_user}:{urllib.parse.quote_plus(_db_password)}@{_db_host}:{_db_port}/{_db_name}"
else:
    DATABASE_URL = f"postgresql://{_safe_user}@{_db_host}:{_db_port}/{_db_name}"

# TLS-TO-POSTGRES: mirrors database.py's own DB_SSLMODE/DB_SSLROOTCERT
# handling exactly, for the same reason -- Alembic's migration engine must
# not silently differ in TLS posture from the app's own runtime engine.
# Defaults to sslmode=prefer (libpq's own default), so this is a no-op
# until DB_SSLMODE/DB_SSLROOTCERT are explicitly set in the environment.
_db_sslmode = os.getenv("DB_SSLMODE", "prefer")
_db_sslrootcert = os.getenv("DB_SSLROOTCERT", "")
CONNECT_ARGS = {"sslmode": _db_sslmode}
if _db_sslrootcert:
    CONNECT_ARGS["sslrootcert"] = _db_sslrootcert

# Import every module that defines a table on models.Base so autogenerate
# sees the full schema. `migration.models` (MigrationJob / migration_jobs)
# shares the same Base but lives in a separate package and is only ever
# imported lazily elsewhere in the app -- without this import here,
# autogenerate would not see that table at all and could propose dropping it.
import models  # noqa: E402
import migration.models  # noqa: E402,F401
target_metadata = models.Base.metadata

# Tables that exist in the live database but are NOT managed by Alembic:
#   - candles_migration: a transient staging table owned entirely by the
#     migration/ package's own bespoke 1D-candle-migration tool (created,
#     populated, and renamed by migration/staging.py + promotion.py as part
#     of its own workflow -- has no SQLAlchemy model on purpose).
#   - candles_aug2021_repair_bak: a manual one-off backup table (has its own
#     extra `backed_up_at` column, not app-managed at all).
#   - the 5 deprecated_intraday_* tables: found during the "PRODUCTION SCHEMA
#     DRIFT RESOLUTION" audit that these tables were renamed live at some
#     point (e.g. `intraday_candles_15min` -> `deprecated_intraday_candles_
#     15min`, per the DB-09 comment in models.py) and Postgres does not
#     rename a table's indexes/PK constraint along with it -- so their index
#     names (ix_intraday_candles_15min_id, intraday_candles_15min_pkey, etc.)
#     are still the OLD pre-rename names, while models.py (naturally) expects
#     names matching the CURRENT table name. compare_metadata sees this as
#     "wrong index, right index missing" and would propose a DROP+CREATE pass
#     across all of them -- structurally pointless churn (the indexes are
#     otherwise identical) on tables that are explicitly frozen/deprecated
#     and out of scope for modification. Excluding them here means Alembic
#     never proposes anything for them at all, matching how the other two
#     unmanaged tables above are already treated.
_UNMANAGED_TABLES = {
    "candles_migration",
    "candles_aug2021_repair_bak",
    "deprecated_intraday_candles_5min",
    "deprecated_intraday_candles_15min",
    "deprecated_intraday_candles_30min",
    "deprecated_intraday_candles_1h",
    "deprecated_intraday_candles_1min",
    "deprecated_intraday_ticks",
}


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table" and name in _UNMANAGED_TABLES:
        return False
    return True


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool, connect_args=CONNECT_ARGS)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
