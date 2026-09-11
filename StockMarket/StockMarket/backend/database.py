import urllib.parse
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

def get_ist_now():
    """Returns current time in IST as a naive datetime (for DB storage/comparison)"""
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)

def get_ist_now_aware():
    """Returns current time in IST as a timezone-aware datetime"""
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

load_dotenv()

# Default to the credentials used in the previous step if not in env
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "stock_data")

# Construct database URL
# If password is set, include it. If not, don't.
SAFE_USER = urllib.parse.quote_plus(DB_USER)
SAFE_HOST = DB_HOST
SAFE_PORT = DB_PORT
SAFE_DB = DB_NAME
if DB_PASSWORD:
    SQLALCHEMY_DATABASE_URL = f"postgresql://{SAFE_USER}:{urllib.parse.quote_plus(DB_PASSWORD)}@{SAFE_HOST}:{SAFE_PORT}/{SAFE_DB}"
else:
    SQLALCHEMY_DATABASE_URL = f"postgresql://{SAFE_USER}@{SAFE_HOST}:{SAFE_PORT}/{SAFE_DB}"

# TLS-TO-POSTGRES (PostgreSQL TLS & network-topology security audit):
# previously no sslmode was ever set anywhere in this app, so every
# connection relied silently on libpq's own default -- sslmode=prefer,
# which (a) falls back to a fully UNENCRYPTED connection with no error/
# warning if the server doesn't offer SSL, and (b) never validates the
# server's certificate even when SSL IS negotiated (only verify-ca/
# verify-full do that), so it offers no real MITM protection either way.
#
# DB_SSLMODE defaults to "prefer" -- i.e. this change is a no-op for every
# environment as configured today (local dev, CI, and production unless
# and until DB_SSLMODE is explicitly set) -- because the real production
# RDS hostname/CA bundle are not knowable from this repository. Once
# that's confirmed (see alembic/README's TLS section), set DB_SSLMODE=
# verify-full and DB_SSLROOTCERT=<path to the RDS CA bundle> in the
# production environment to get real certificate-validated encryption.
# Never invent a CA path/bundle here -- an unverifiable one is worse than
# an explicit "not yet configured".
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")
DB_SSLROOTCERT = os.getenv("DB_SSLROOTCERT", "")

_connect_args = {
    "connect_timeout": 5,
    "options": "-c statement_timeout=30000",
    "sslmode": DB_SSLMODE,
}
if DB_SSLROOTCERT:
    _connect_args["sslrootcert"] = DB_SSLROOTCERT

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    # DO NOT reduce pool_size+max_overflow below 35 total.
    # History: raised from 10/20 (ceiling=30) because startup background jobs
    # (daily prefill, prewarm, close sync) exhausted the pool — every request
    # then blocked for 30s and failed with "QueuePool limit … reached".
    # PostgreSQL max_connections is now 80; app pool ceiling (50) + system/autovacuum
    # reserves (~30) = 80. Adjust both together if changing either value.
    pool_size=20,
    max_overflow=30,
    pool_timeout=10,      # fail fast instead of stalling a request for 30s
    pool_pre_ping=True,
    pool_recycle=3600,
    # HARDEN-02: previously no bound at all -- a hung TCP connect or a
    # runaway query could block indefinitely. connect_timeout=5 is generous
    # for a same-region TCP+auth handshake (local Postgres or same-AZ RDS).
    # statement_timeout=30000ms bounds each INDIVIDUAL SQL statement, not a
    # whole transaction -- a transaction doing many small fast statements is
    # unaffected even if it runs for minutes overall. Verified before adding
    # this: Alembic migrations use their own separate engine (alembic/env.py
    # builds its own create_engine(), not this one) so are entirely
    # unaffected; one-off maintenance/backfill scripts mostly build their
    # own separate engines too; live app code (retention/recovery/chart
    # services, all reached via this engine) filters every candles query by
    # indexed (ticker, timeframe, timestamp), not full-table scans. No
    # single statement in the live request/job path was found that would
    # legitimately need more than 30s.
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
