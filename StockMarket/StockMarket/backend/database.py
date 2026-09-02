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
    pool_recycle=3600
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
