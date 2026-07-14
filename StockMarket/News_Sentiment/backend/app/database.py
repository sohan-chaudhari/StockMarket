from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from motor.motor_asyncio import AsyncIOMotorClient
import redis
import logging
from app.config import settings

logger = logging.getLogger(__name__)

# PostgreSQL (Structured data) — lazy init
_engine = None
_SessionLocal = None
Base = declarative_base()


def get_engine():
    global _engine
    if _engine is None:
        try:
            _engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
        except Exception as e:
            logger.warning(f"PostgreSQL engine creation failed: {e}")
            _engine = None
    return _engine


def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        eng = get_engine()
        if eng:
            _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    return _SessionLocal


def get_db():
    """Dependency for PostgreSQL session (returns None if DB unavailable)"""
    SessionLocal = get_session_local()
    if SessionLocal is None:
        return None
    try:
        db = SessionLocal()
        yield db
        db.close()
    except Exception as e:
        logger.warning(f"PostgreSQL session failed: {e}")
        yield None


# MongoDB (Unstructured data) — lazy init
_mongo_client = None
_mongodb = None


def get_mongodb_instance():
    global _mongo_client, _mongodb
    if _mongo_client is None:
        try:
            _mongo_client = AsyncIOMotorClient(settings.MONGODB_URI)
            _mongodb = _mongo_client.get_database()
        except Exception as e:
            logger.warning(f"MongoDB connection failed: {e}")
    return _mongodb


async def get_mongodb():
    """Dependency for MongoDB"""
    return get_mongodb_instance()


# Redis (Caching) — lazy init
_redis_client = None


def get_redis_instance():
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
    return _redis_client


def get_redis():
    """Dependency for Redis"""
    return get_redis_instance()


engine = get_engine()  # compatibility alias


class _MongoDBProxy:
    """Proxy that delegates attribute access to the current MongoDB instance.
    
    Usage:  mongodb.news_articles.find(...)
    Ensures the latest MongoDB instance is used even if initialized later.
    """
    def __getattr__(self, name):
        db = get_mongodb_instance()
        if db is None:
            raise AttributeError("MongoDB not available — call get_mongodb_instance() first")
        return getattr(db, name)


mongodb = _MongoDBProxy()
