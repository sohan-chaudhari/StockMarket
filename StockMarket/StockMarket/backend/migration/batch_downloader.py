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
from migration.identity import TickerIdentity
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

    def run_tier(self, tier_index: int, tier_cfg: TierConfig, tickers: List["TickerIdentity"], progress_callback: Optional[Callable] = None,
                 required_start_override: Optional[date] = None):
        """`tickers` carries resolved (ticker, exchange) identities, not bare
        strings -- see migration/identity.py. MigrationJob tracking is still
        keyed by the ticker string alone (unchanged schema), but the exchange
        travels with each queue item so the actual Angel One fetch call uses
        the correct instrument, not always the NSE default.

        required_start_override: 1D tier only (--deep-backfill-days). When
        given, the per-ticker gap-aware narrowing below (existing_start ->
        compute_1d_required_range) uses THIS as required_start instead of
        the real 730-day retention policy, so a deep-backfill run only
        requests each ticker's missing OLDER gap -- never re-fetches the
        already-present recent window. The real retention policy in
        migration.yaml is never touched by this."""
        date_range = self._tier_ranges.get(tier_cfg.timeframe)
        tf = tier_cfg.timeframe
        print(f"\n{'='*60}")
        print(f"TIER {tier_index}: {tf} ({tier_cfg.fetch_method})")
        print(f"  Date range: {date_range[0]} to {date_range[1]}")
        print(f"  Tickers: {len(tickers)}")
        print(f"{'='*60}\n")

        db = SessionLocal()
        try:
            for identity in tickers:
                self._tracker.initialize_ticker(db, identity.ticker, tier_index, tf)
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
                args=(worker_id, tier_index, tier_cfg, date_range, ticker_queue, progress_callback, required_start_override),
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
        required_start_override: Optional[date] = None,
    ):
        while not self._stop_event.is_set():
            try:
                identity: TickerIdentity = queue.get_nowait()
            except Empty:
                return
            ticker = identity.ticker
            exchange = identity.exchange

            db = SessionLocal()
            existing_job = None
            skip = False
            try:
                existing_job = db.query(MigrationJob).filter(
                    MigrationJob.ticker == ticker,
                    MigrationJob.tier == tier_index,
                ).first()

                # Terminal statuses are never re-fetched. KNOWN_ABSENCES
                # belongs here alongside DONE: its remaining gaps were already
                # proven unfillable by a SUCCESSFUL Angel One response, so
                # re-requesting them would loop forever on the same empty answer.
                #
                # Exception: a 1D deep-backfill run (required_start_override
                # set) targets a DEEPER required_start than whatever this job
                # row was last marked terminal against -- that terminal status
                # only proves the ticker satisfied the SHALLOWER 730-day
                # policy, not this run's target. Skip the blanket short-circuit
                # here and let the real per-ticker gap check below (which reads
                # the ticker's actual earliest `candles` row, not this stale
                # flag) decide whether there's a genuine older gap to fetch.
                is_1d_deep_backfill = tier_cfg.timeframe == "1D" and required_start_override is not None
                if existing_job and existing_job.status in self._tracker.TERMINAL_STATUSES and not is_1d_deep_backfill:
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

                ticker_date_range = date_range
                if tier_cfg.timeframe == "1D":
                    # Per-ticker gap-aware narrowing: the tier-global date_range
                    # (same for every ticker) would blindly re-request whatever
                    # a ticker already has in `candles` -- item B of the 1D
                    # migration prep requires fetching only the missing history.
                    # See migration/config.py::compute_1d_required_range.
                    #
                    # SECOND BUG FIX (found alongside the validation bug above,
                    # by the same real controlled execution): required_start
                    # was being unpacked from `date_range[0]` -- the coarse,
                    # month-based tier-global estimate from migration.yaml
                    # (via compute_tier_date_ranges), NOT the exact calendar-
                    # day 730-day requirement. That silently diverged from
                    # what run_1d_migration.py's dry-run plans and reports
                    # (which correctly calls get_1d_retention_days()) --
                    # the real fetch could request a different, wrong range
                    # than what was previewed and approved. required_start
                    # must be computed the exact same way here as in
                    # migration/plan_1d.py, not derived from the tier-global
                    # range at all.
                    from migration.config import compute_1d_required_range, get_1d_retention_days
                    from models import Candle
                    existing_earliest = db.query(Candle.timestamp).filter(
                        Candle.ticker == ticker, Candle.timeframe == "1D",
                    ).order_by(Candle.timestamp.asc()).first()
                    existing_start = existing_earliest[0].date() if existing_earliest else None
                    required_start = required_start_override if required_start_override is not None else (
                        date.today() - timedelta(days=get_1d_retention_days())
                    )
                    narrowed = compute_1d_required_range(
                        required_start=required_start, today=date.today(), existing_start=existing_start,
                    )
                    if narrowed is None:
                        # Already satisfies the 730-day requirement -- nothing to fetch.
                        self._tracker.mark_done(db, existing_job, 0, 0, "", "")
                        db.commit()
                        if progress_callback:
                            progress_callback(ticker, tier_index, "DONE", 0)
                        continue
                    ticker_date_range = narrowed

                # Per-tier chunk width: 1D overrides the global 90-day default
                # with its own empirically verified 730-day span, while every
                # intraday tier keeps the global default untouched.
                chunk_days = tier_cfg.effective_max_date_range_days(self._cfg.max_date_range_days)
                date_chunks = self._chunk_date_range(ticker_date_range, chunk_days)
                all_source_candles = []

                for chunk_idx, (chunk_start, chunk_end) in enumerate(date_chunks):
                    success, candles, err, attempts = self._fetch_mgr.fetch_with_retry(
                        ticker, tier_cfg.angel_interval, chunk_start, chunk_end, exchange=exchange
                    )
                    if attempts > 1:
                        # Real retries happened inside fetch_with_retry (rate
                        # limit / transient network error) -- surface them
                        # here rather than relying on job.retry_count, which
                        # only tracks whole-job re-attempts across separate
                        # runs, not per-chunk retries within this one.
                        self._update_stats(total_retries=attempts - 1)
                    if not success:
                        self._update_stats(failed_api_calls=1)
                        self._tracker.mark_failed(db, existing_job, err or f"Fetch failed on chunk {chunk_idx}")
                        db.commit()
                        self._append_error(ticker, tier_cfg.timeframe, err or f"Fetch failed on chunk {chunk_idx}")
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

                # BUG FIX (found by a real controlled 1D execution test, not
                # simulation): this validated against the tier-global
                # `date_range` (e.g. migration.yaml's coarse month-based
                # estimate) instead of `ticker_date_range` (the per-ticker
                # narrowed range actually requested/fetched -- see above).
                # For the 1D tier, the two differ whenever a ticker already
                # has some recent 1D history: the real fetch correctly
                # requests only the older gap, but the validator was
                # checking those (correct, older) timestamps against a
                # range that didn't cover them, so the "date_range" check
                # failed for every 1D ticker with any existing coverage,
                # rejecting otherwise-valid data before it ever reached
                # staging.
                val_result = self._validator.validate_source(source_candles, tier_cfg.timeframe, ticker_date_range)
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
        """Delegates to migration.config.chunk_date_range -- the single shared
        chunking implementation (see that function's docstring for why)."""
        from migration.config import chunk_date_range
        start, end = date_range
        return chunk_date_range(start, end, max_days)

    def _append_error(self, ticker: str, timeframe: str, error: str):
        with self._stats_lock:
            self._stats["errors"].append({
                "ticker": ticker,
                "timeframe": timeframe,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            })
