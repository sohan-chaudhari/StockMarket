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
        # MIG-01: enforce the checksum gate — if checksums diverge the DB
        # write was silently corrupted; fail the job so it gets retried
        # rather than masking bad data under a DONE status.
        if checksum_src and checksum_db and checksum_src != checksum_db:
            self.mark_failed(
                db, job,
                f"Checksum mismatch: src={checksum_src} db={checksum_db}"
            )
            print(f"[Migration] CHECKSUM MISMATCH for {job.ticker} tier={job.tier} "
                  f"— marked FAILED for retry (src={checksum_src}, db={checksum_db})")
            return
        job.status = "DONE"
        job.rows_fetched = rows_fetched
        job.rows_inserted = rows_inserted
        job.checksum_src = checksum_src
        job.checksum_db = checksum_db
        job.completed_at = datetime.now()
        job.last_updated = datetime.now()

    # Terminal statuses: a job in one of these is finished and must NOT be
    # re-fetched by a later run. get_pending_jobs() only picks up
    # PENDING/FAILED, and BatchDownloader._worker_loop skips these.
    #
    # MigrationJob.status is Column(String(20)) -- every value here MUST fit
    # in 20 characters. The first attempt used the far more descriptive
    # "COMPLETE_WITH_KNOWN_ABSENCES" (29 chars); Postgres rejected the UPDATE
    # with StringDataRightTruncation *after* a full 1,001-ticker migration had
    # already fetched and promoted, losing only the status labels. Checking
    # that a column is a String is not the same as checking its width.
    # test_status_values_fit_column asserts this invariant.
    KNOWN_ABSENCES = "KNOWN_ABSENCES"
    TERMINAL_STATUSES = ("DONE", KNOWN_ABSENCES)

    def mark_complete_with_known_absences(self, db: Session, job: MigrationJob, detail: str):
        """The ticker's fetch fully SUCCEEDED (every requested chunk returned
        a successful response), yet some sessions are still absent because
        Angel One's own dataset does not contain them -- e.g. illiquid
        securities with genuine no-trade days, and cases where 5m data proves
        the security traded but no 1D candle exists.

        This is deliberately NOT 'DONE' (the history is genuinely incomplete
        and that must stay visible) and NOT 'FAILED' (nothing failed, and
        retrying would re-request the identical range, get the identical
        empty-but-successful answer, and fail forever). Stored in the
        existing status String column -- no schema change required.

        Only reachable when zero chunks failed; a ticker with any real fetch
        failure stays FAILED and remains eligible for retry."""
        job.status = self.KNOWN_ABSENCES
        job.error_message = detail
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

    def recover_stuck_fetching(self, db: Session, older_than_minutes: int = 30) -> int:
        """MIG-01: Reset jobs stuck in FETCHING (app crashed mid-migration)
        back to PENDING so they are retried on the next resume.

        A job should complete in well under a minute.  Any FETCHING row
        older than *older_than_minutes* was left stranded by a crash and
        will never self-recover — it just holds the slot forever with
        retry_count=0.
        """
        from datetime import timedelta as _td
        cutoff = datetime.now() - _td(minutes=older_than_minutes)
        stuck = db.execute(text("""
            SELECT id FROM migration_jobs
            WHERE status = 'FETCHING'
              AND (started_at IS NULL OR started_at < :cutoff)
        """), {"cutoff": cutoff}).fetchall()
        if not stuck:
            return 0
        ids = [r[0] for r in stuck]
        db.execute(text("""
            UPDATE migration_jobs
            SET status = 'PENDING', last_updated = NOW()
            WHERE id = ANY(:ids)
        """), {"ids": ids})
        print(f"[Migration] Recovered {len(ids)} stuck FETCHING job(s) → PENDING")
        return len(ids)

    def get_failed_jobs(self, db: Session) -> List[dict]:
        rows = db.execute(text("""
            SELECT ticker, tier, timeframe, error_message, retry_count
            FROM migration_jobs
            WHERE status = 'FAILED'
            ORDER BY ticker, tier
        """)).fetchall()
        return [{"ticker": r[0], "tier": r[1], "timeframe": r[2], "error": r[3], "retries": r[4]} for r in rows]
