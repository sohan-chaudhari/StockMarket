from sqlalchemy import Column, Integer, String, SmallInteger, DateTime, BigInteger, Text, UniqueConstraint, Index
from sqlalchemy.sql import func
from database import Base


class MigrationJob(Base):
    __tablename__ = "migration_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ticker = Column(String(50), nullable=False, index=True)
    tier = Column(SmallInteger, nullable=False)
    timeframe = Column(String(5), nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")

    rows_fetched = Column(BigInteger, default=0)
    rows_inserted = Column(BigInteger, default=0)
    checksum_src = Column(String(64), nullable=True)
    checksum_db = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(SmallInteger, default=0)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("ticker", "tier", name="uix_migration_job"),
        # DB-08: get_pending_jobs() filters on exactly (tier, status) every
        # batch of every migration run -- no index covered that pair before.
        Index("idx_migration_jobs_tier_status", "tier", "status"),
    )

    def __repr__(self):
        return f"<MigrationJob {self.ticker} tier={self.tier} ({self.timeframe}) status={self.status}>"
