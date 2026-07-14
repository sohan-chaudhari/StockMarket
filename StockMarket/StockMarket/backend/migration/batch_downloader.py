import time
import threading
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple, Callable
from queue import Queue, Empty
from sqlalchemy.orm import Session

from database import SessionLocal
from migration.config import MigrationConfig, TierConfig, compute_tier_date_ranges
from migration.fetch_manager import AngelOneFetchManager
from migration.progress_tracker import ProgressTracker
from migration.validator import MigrationValidator, compute_checksum
from migration.models import MigrationJob
from resampler import CandleResampler


class BatchDownloader:
    def __init__(self, cfg: MigrationConfig):
        self._cfg = cfg
        self._fetch_mgr = AngelOneFetchManager(cfg)
        self._tracker = ProgressTracker(cfg)
        self._validator = MigrationValidator()
        self._resampler = CandleResampler()
        self._tier_ranges = compute_tier_date_ranges(cfg)
        self._stop_event = threading.Event()

        self._stats = {
            "total_api_calls": 0,
            "failed_api_calls": 0,
            "total_retries": 0,
            "total_candles_fetched": 0,
            "total_candles_inserted": 0,
            "total_checksum_failures": 0,
            "errors": [],
        }
        self._stats_lock = threading.Lock()

    def _update_stats(self, **kwargs):
        with self._stats_lock:
            for k, v in kwargs.items():
                if k in self._stats:
                    self._stats[k] += v

    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    def run_tier(self, tier_index: int, tier_cfg: TierConfig, tickers: List[str], progress_callback: Optional[Callable] = None):
        date_range = self._tier_ranges.get(tier_cfg.timeframe)
        tf = tier_cfg.timeframe
        print(f"\n{'='*60}")
        print(f"TIER {tier_index}: {tf} ({tier_cfg.fetch_method})")
        print(f"  Date range: {date_range[0]} to {date_range[1]}")
        print(f"  Tickers: {len(tickers)}")
        print(f"{'='*60}\n")

        db = SessionLocal()
        try:
            for ticker in tickers:
                self._tracker.initialize_ticker(db, ticker, tier_index, tf)
            db.commit()
        finally:
            db.close()

        ticker_queue = Queue()
        for t in tickers:
            ticker_queue.put(t)

        threads = []
        for worker_id in range(self._cfg.workers):
            t = threading.Thread(
                target=self._worker_loop,
                args=(worker_id, tier_index, tier_cfg, date_range, ticker_queue, progress_callback),
                daemon=True,
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

    def _worker_loop(
        self,
        worker_id: int,
        tier_index: int,
        tier_cfg: TierConfig,
        date_range: Tuple[date, date],
        queue: Queue,
        progress_callback: Optional[Callable],
    ):
        while not self._stop_event.is_set():
            try:
                ticker = queue.get_nowait()
            except Empty:
                return

            db = SessionLocal()
            existing_job = None
            skip = False
            try:
                existing_job = db.query(MigrationJob).filter(
                    MigrationJob.ticker == ticker,
                    MigrationJob.tier == tier_index,
                ).first()

                if existing_job and existing_job.status == "DONE":
                    self._update_stats(skipped=1)
                    if progress_callback:
                        progress_callback(ticker, tier_index, "SKIPPED", 0)
                    continue

                if existing_job and existing_job.status == "FAILED":
                    if (existing_job.retry_count or 0) >= self._cfg.retry_max:
                        if progress_callback:
                            progress_callback(ticker, tier_index, "FAILED", 0)
                        continue

                if existing_job:
                    self._tracker.mark_fetching(db, existing_job)
                    db.commit()
                else:
                    self._tracker.initialize_ticker(db, ticker, tier_index, tier_cfg.timeframe)
                    db.flush()
                    existing_job = db.query(MigrationJob).filter(
                        MigrationJob.ticker == ticker,
                        MigrationJob.tier == tier_index,
                    ).first()
                    self._tracker.mark_fetching(db, existing_job)
                    db.commit()

                date_chunks = self._chunk_date_range(date_range, self._cfg.max_date_range_days)
                all_source_candles = []

                for chunk_idx, (chunk_start, chunk_end) in enumerate(date_chunks):
                    success, candles, err = self._fetch_mgr.fetch_with_retry(
                        ticker, tier_cfg.angel_interval, chunk_start, chunk_end
                    )
                    if not success:
                        self._update_stats(failed_api_calls=1)
                        self._tracker.mark_failed(db, existing_job, err or "Fetch failed on chunk {chunk_idx}")
                        db.commit()
                        self._append_error(ticker, tier_cfg.timeframe, err or "Fetch failed on chunk {chunk_idx}")
                        if progress_callback:
                            progress_callback(ticker, tier_index, "FAILED", 0)
                        skip = True
                        break

                    all_source_candles.extend(candles)
                    self._update_stats(total_api_calls=1)

                if skip:
                    continue

                if not all_source_candles:
                    self._tracker.mark_done(db, existing_job, 0, 0, "", "")
                    db.commit()
                    if progress_callback:
                        progress_callback(ticker, tier_index, "DONE", 0)
                    continue

                if tier_cfg.fetch_method == "resample":
                    source_candles = self._resampler.resample_5m_to(all_source_candles, tier_cfg.timeframe)
                    if not source_candles:
                        self._tracker.mark_failed(db, existing_job, "Resampling produced 0 candles")
                        db.commit()
                        self._append_error(ticker, tier_cfg.timeframe, "Resampling produced 0 candles")
                        if progress_callback:
                            progress_callback(ticker, tier_index, "FAILED", 0)
                        continue
                else:
                    source_candles = all_source_candles

                if not source_candles:
                    self._tracker.mark_done(db, existing_job, 0, 0, "", "")
                    db.commit()
                    if progress_callback:
                        progress_callback(ticker, tier_index, "DONE", 0)
                    continue

                val_result = self._validator.validate_source(source_candles, tier_cfg.timeframe, date_range)
                if not val_result.passed:
                    err_detail = val_result.summary()
                    self._tracker.mark_failed(db, existing_job, f"Validation failed: {err_detail}")
                    db.commit()
                    self._append_error(ticker, tier_cfg.timeframe, f"Validation failed: {err_detail}")
                    if progress_callback:
                        progress_callback(ticker, tier_index, "FAILED", 0)
                    continue

                checksum_src = compute_checksum(source_candles)

                self._clean_slate(ticker, tier_cfg.timeframe, db)
                rows_inserted = self._bulk_insert(ticker, tier_cfg.timeframe, source_candles, db)
                if rows_inserted < 0:
                    self._tracker.mark_failed(db, existing_job, "Bulk insert failed")
                    db.commit()
                    self._append_error(ticker, tier_cfg.timeframe, "Bulk insert failed")
                    if progress_callback:
                        progress_callback(ticker, tier_index, "FAILED", 0)
                    continue

                self._update_stats(total_candles_fetched=len(source_candles), total_candles_inserted=rows_inserted)

                db_candles = self._readback_candles(ticker, tier_cfg.timeframe, db)
                if db_candles is None:
                    self._tracker.mark_failed(db, existing_job, "Readback failed")
                    db.commit()
                    self._append_error(ticker, tier_cfg.timeframe, "Readback failed")
                    if progress_callback:
                        progress_callback(ticker, tier_index, "FAILED", 0)
                    continue

                checksum_db = compute_checksum(db_candles)
                self._tracker.mark_done(db, existing_job, len(source_candles), rows_inserted, checksum_src, checksum_db)
                db.commit()

                self._update_stats(total_retries=existing_job.retry_count or 0)

                if progress_callback:
                    progress_callback(ticker, tier_index, "DONE", rows_inserted)

            except Exception as e:
                db.rollback()
                err_msg = str(e)
                self._update_stats(failed_api_calls=1)
                self._append_error(ticker, tier_cfg.timeframe, err_msg)
                if existing_job:
                    self._tracker.mark_failed(db, existing_job, err_msg)
                    db.commit()
                if progress_callback:
                    progress_callback(ticker, tier_index, "FAILED", 0)
            finally:
                db.close()

    def _clean_slate(self, ticker: str, timeframe: str, db: Session):
        from sqlalchemy import text
        table_name = self._cfg.staging_table
        try:
            db.execute(text(f"DELETE FROM {table_name} WHERE ticker = :t AND timeframe = :tf"),
                       {"t": ticker, "tf": timeframe})
            db.commit()
        except Exception:
            db.rollback()

    def _bulk_insert(self, ticker: str, timeframe: str, candles: List[Dict], db: Session) -> int:
        try:
            from sqlalchemy import text

            table_name = self._cfg.staging_table
            inserted = 0
            for c in candles:
                ts = c["timestamp"]
                if isinstance(ts, datetime):
                    insert_ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
                else:
                    insert_ts = datetime.fromisoformat(str(ts)).replace(tzinfo=None)

                stmt = text(f"""
                    INSERT INTO {table_name} (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
                    VALUES (:ticker, :timeframe, :ts, :open, :high, :low, :close, :volume, TRUE, 'ANGELONE', TRUE)
                    ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING
                """)
                result = db.execute(stmt, {
                    "ticker": ticker,
                    "timeframe": timeframe,
                    "ts": insert_ts,
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": int(c.get("volume", 0)),
                })
                if result.rowcount > 0:
                    inserted += 1
            db.commit()
            return inserted
        except Exception as e:
            db.rollback()
            print(f"[BulkInsert] Error: {e}")
            return -1

    def _readback_candles(self, ticker: str, timeframe: str, db: Session):
        try:
            from sqlalchemy import text

            table_name = self._cfg.staging_table
            rows = db.execute(text(f"""
                SELECT timestamp, open, high, low, close, volume
                FROM {table_name}
                WHERE ticker = :ticker AND timeframe = :timeframe
                ORDER BY timestamp ASC
            """), {"ticker": ticker, "timeframe": timeframe}).fetchall()

            return [
                {
                    "timestamp": r[0],
                    "open": r[1],
                    "high": r[2],
                    "low": r[3],
                    "close": r[4],
                    "volume": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            print(f"[Readback] Error: {e}")
            return None

    @staticmethod
    def _chunk_date_range(date_range: Tuple[date, date], max_days: int) -> List[Tuple[date, date]]:
        start, end = date_range
        if (end - start).days <= max_days:
            return [(start, end)]

        chunks = []
        current = start
        while current < end:
            chunk_end = min(current + timedelta(days=max_days), end)
            chunks.append((current, chunk_end))
            current = chunk_end + timedelta(days=1)
        return chunks

    def _append_error(self, ticker: str, timeframe: str, error: str):
        with self._stats_lock:
            self._stats["errors"].append({
                "ticker": ticker,
                "timeframe": timeframe,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            })
