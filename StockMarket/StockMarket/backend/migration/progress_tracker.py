from datetime import datetime
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import SessionLocal
from migration.models import MigrationJob
from migration.config import MigrationConfig, TierConfig


class ProgressTracker:
    def __init__(self, cfg: MigrationConfig):
        self._cfg = cfg
        self._ensure_table()

    def _ensure_table(self):
        db = SessionLocal()
        try:
            from models import Base as StockBase
            from migration.models import MigrationJob
            StockBase.metadata.create_all(bind=db.get_bind())
        finally:
            db.close()

    def initialize_ticker(self, db: Session, ticker: str, tier_index: int, tf: str):
        existing = db.query(MigrationJob).filter(
            MigrationJob.ticker == ticker,
            MigrationJob.tier == tier_index,
        ).first()
        if not existing:
            job = MigrationJob(
                ticker=ticker,
                tier=tier_index,
                timeframe=tf,
                status="PENDING",
            )
            db.add(job)

    def get_pending_jobs(self, db: Session, tier_index: int, limit: int = 100) -> List[MigrationJob]:
        return db.query(MigrationJob).filter(
            MigrationJob.tier == tier_index,
            MigrationJob.status.in_(["PENDING", "FAILED"]),
        ).order_by(MigrationJob.ticker).limit(limit).all()

    def mark_fetching(self, db: Session, job: MigrationJob):
        job.status = "FETCHING"
        job.started_at = datetime.now()
        job.last_updated = datetime.now()

    def mark_validating(self, db: Session, job: MigrationJob):
        job.status = "VALIDATING"
        job.last_updated = datetime.now()

    def mark_inserting(self, db: Session, job: MigrationJob):
        job.status = "INSERTING"
        job.last_updated = datetime.now()

    def mark_done(
        self,
        db: Session,
        job: MigrationJob,
        rows_fetched: int,
        rows_inserted: int,
        checksum_src: str,
        checksum_db: str,
    ):
        job.status = "DONE"
        job.rows_fetched = rows_fetched
        job.rows_inserted = rows_inserted
        job.checksum_src = checksum_src
        job.checksum_db = checksum_db
        job.completed_at = datetime.now()
        job.last_updated = datetime.now()

    def mark_failed(self, db: Session, job: MigrationJob, error: str):
        job.status = "FAILED"
        job.error_message = error
        job.retry_count = (job.retry_count or 0) + 1
        job.last_updated = datetime.now()

    def increment_retry(self, db: Session, job: MigrationJob, error: str):
        if job.retry_count is None:
            job.retry_count = 0
        job.retry_count += 1
        job.error_message = error
        job.status = "PENDING" if job.retry_count < self._cfg.retry_max else "FAILED"
        job.last_updated = datetime.now()

    def get_tier_summary(self, db: Session, tier_index: int) -> dict:
        row = db.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'DONE') AS done,
                COUNT(*) FILTER (WHERE status = 'FAILED') AS failed,
                COUNT(*) FILTER (WHERE status = 'PENDING') AS pending,
                COALESCE(SUM(rows_inserted), 0) AS total_rows
            FROM migration_jobs
            WHERE tier = :tier
        """), {"tier": tier_index}).fetchone()
        return {
            "total": row[0],
            "done": row[1],
            "failed": row[2],
            "pending": row[3],
            "total_rows": row[4],
        }

    def get_all_tier_summaries(self, db: Session) -> dict:
        rows = db.execute(text("""
            SELECT
                tier,
                timeframe,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'DONE') AS done,
                COUNT(*) FILTER (WHERE status = 'FAILED') AS failed,
                COUNT(*) FILTER (WHERE status = 'PENDING') AS pending,
                COALESCE(SUM(rows_inserted), 0) AS total_rows,
                COALESCE(SUM(retry_count), 0) AS total_retries
            FROM migration_jobs
            GROUP BY tier, timeframe
            ORDER BY tier
        """)).fetchall()
        return [{
            "tier": r[0],
            "timeframe": r[1],
            "total": r[2],
            "done": r[3],
            "failed": r[4],
            "pending": r[5],
            "total_rows": r[6],
            "total_retries": r[7],
        } for r in rows]

    def get_failed_jobs(self, db: Session) -> List[dict]:
        rows = db.execute(text("""
            SELECT ticker, tier, timeframe, error_message, retry_count
            FROM migration_jobs
            WHERE status = 'FAILED'
            ORDER BY ticker, tier
        """)).fetchall()
        return [{"ticker": r[0], "tier": r[1], "timeframe": r[2], "error": r[3], "retries": r[4]} for r in rows]
