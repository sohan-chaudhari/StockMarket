"""
RetentionService
================
Rolling data lifecycle engine.

Converts older intraday candles to progressively coarser timeframes based on
a configurable retention policy (loaded from config/retention_policy.yaml).

Progressive NSE-session pyramid (never calendar days):
     5m  → 15m : compress 5m  older than  60 trading sessions
     15m → 30m : compress 15m older than 120 trading sessions
     30m → 1h  : compress 30m older than 180 trading sessions
     1h  → 4h  : compress 1h  older than 240 trading sessions
     4h  is the FINAL intraday tier (no 4h→1D here; daily/weekly handled separately)

Key rules:
  - Atomic per-batch: INSERT targets → verify → DELETE source → COMMIT or ROLLBACK
  - Idempotent: ON CONFLICT DO NOTHING on insert
  - Verify before delete: source rows are removed only after the target rows
    are confirmed present in the same transaction
  - Keyset pagination (ticker, timestamp), never OFFSET on a shrinking dataset;
    restart-safe because processed source rows are gone and the cursor only moves forward
  - No same-run cascade: rules are executed from the top tier downward, so rows
    created in this run are never consumed by a downstream rule in the SAME run
  - Per-ticker resampling: batches never mix tickers (prevents cross-ticker OHLC corruption)
  - Live window protection: NEVER converts data from the current trading day
  - Checkpoint resume: tracks last_ticker per rule for crash recovery
  - Session-accurate cutoffs via nse_calendar.is_trading_day
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass

from config import load_retention_policy
from aggregator import ist_now_naive


@dataclass
class RetentionRule:
    source_tf: str
    target_tf: str
    after_days: int = 0
    after_trading_sessions: Optional[int] = None
    batch_size: int = 200


class RetentionService:
    def __init__(self, db_session_factory, resample_svc):
        self._db_factory = db_session_factory
        self._resample_svc = resample_svc
        self._lock = threading.Lock()
        self._running = False
        self._last_run: Optional[datetime] = None
        self._rows_converted = 0
        self._errors = 0

    # ── Policy ──────────────────────────────────────────────────────────
    def load_policy(self) -> List[RetentionRule]:
        cfg = load_retention_policy()
        return [
            RetentionRule(
                source_tf=entry["source_tf"],
                target_tf=entry["target_tf"],
                after_days=entry.get("after_days", 0),
                after_trading_sessions=entry.get("after_trading_sessions"),
                batch_size=entry.get("batch_size", 200),
            )
            for entry in cfg.get("retention_policy", [])
        ]

    @staticmethod
    def _session_cutoff(sessions: int, now: datetime) -> datetime:
        """Midnight of the Nth prior NSE trading session (exclusive of today)."""
        from exchange_calendar import nse_calendar
        count = 0
        current = now.date() - timedelta(days=1)
        while count < sessions:
            if nse_calendar.is_trading_day(current):
                count += 1
            if count >= sessions:
                break
            current -= timedelta(days=1)
        return datetime.combine(current, datetime.min.time())

    def _compute_cutoff(self, rule: RetentionRule, now: datetime) -> datetime:
        if rule.after_trading_sessions:
            return self._session_cutoff(rule.after_trading_sessions, now)
        raw = (now - timedelta(days=rule.after_days)).replace(hour=0, minute=0, second=0, microsecond=0)
        # B-1: Align to complete calendar periods so no partial week/month is ever
        # promoted.  Without this, a week or month split by the rolling cutoff
        # produces a partial target candle; subsequent source rows are then deleted
        # without being reflected in the target — permanent silent data loss.
        #
        # 1D→1W: floor raw cutoff to the Monday of its ISO week.
        # No 1D candle from the boundary week is eligible until the whole week is
        # past the 730-day window (i.e. the following Monday's cutoff).
        if rule.source_tf == "1D" and rule.target_tf == "1W":
            return raw - timedelta(days=raw.weekday())  # weekday(): Mon=0 … Sun=6
        # 1W→1M: floor raw cutoff to the first day of its calendar month.
        # No 1W candle from the boundary month is eligible until the next month's
        # first-day cutoff.
        if rule.source_tf == "1W" and rule.target_tf == "1M":
            return raw.replace(day=1)
        return raw

    # ── Cycle ───────────────────────────────────────────────────────────
    def run_cycle(self):
        if self._running:
            print("[Retention] Already running, skipping")
            return
        self._running = True
        start_time = datetime.now()
        print(f"[Retention] Starting cycle at {start_time}")
        total_rows = 0

        try:
            now = ist_now_naive()
            rules = self.load_policy()

            # Reverse order (top tier first): 1h→4h, 30m→1h, 15m→30m, 5m→15m.
            # Guarantees no same-run cascade — rows created by 5m→15m are never
            # consumed by 15m→30m within the same run.
            for rule in reversed(rules):
                cutoff = self._compute_cutoff(rule, now)

                live_window_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
                if cutoff >= live_window_start:
                    print(f"[Retention] Skipping {rule.source_tf}->{rule.target_tf}: cutoff {cutoff.date()} is within live window")
                    continue

                rows = self._downgrade(rule, cutoff)
                total_rows += rows

            self._last_run = datetime.now()
            self._rows_converted = total_rows
            print(f"[Retention] Cycle complete: {total_rows} rows converted in {(datetime.now() - start_time).total_seconds():.1f}s")

        except Exception as e:
            self._errors += 1
            print(f"[Retention] Cycle FAILED: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._running = False

    # ── Downgrade ───────────────────────────────────────────────────────
    def _downgrade(self, rule: RetentionRule, cutoff: datetime) -> int:
        from models import Candle, RetentionJob
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        db = self._db_factory()
        job_id = None
        total_source = 0
        total_target = 0
        last_ticker = None
        job_type = f"{rule.source_tf}_to_{rule.target_tf}"
        try:
            # DB-05: resume from an existing crashed-mid-run job for this
            # rule today instead of always starting fresh. _checkpoint_job()
            # below was already writing last_ticker/rows_source/rows_target
            # on every ticker, but nothing ever read it back -- a process
            # crash left the job stuck at status="RUNNING" (the column
            # default; only _finish_job ever changes it) and the next cycle
            # silently redid all prior work from scratch. Idempotent inserts
            # made that safe, just wasteful -- this makes the documented
            # "checkpoint resume" claim in the module docstring actually true.
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

            # B-3: Mark past-day RUNNING rows as FAILED so they don't accumulate
            # indefinitely and mislead monitoring dashboards.  Only rows strictly
            # before today_start are touched; today's own RUNNING job (the one we
            # are about to resume below, if any) is never affected.
            from sqlalchemy import text as _sa_text
            try:
                db.execute(
                    _sa_text(
                        "UPDATE retention_jobs "
                        "SET status='FAILED', end_time=:now, "
                        "    error_message='Abandoned (process restart)' "
                        "WHERE job_type=:jt AND status='RUNNING' AND start_time < :today"
                    ),
                    {"now": datetime.now(), "jt": job_type, "today": today_start},
                )
                db.commit()
            except Exception:
                db.rollback()

            existing = db.query(RetentionJob).filter(
                RetentionJob.job_type == job_type,
                RetentionJob.status == "RUNNING",
                RetentionJob.start_time >= today_start,
            ).order_by(RetentionJob.id.desc()).first()
            if existing is not None:
                job_id = existing.id
                last_ticker = existing.last_ticker
                total_source = int(existing.rows_source) if isinstance(existing.rows_source, (int, float)) else 0
                total_target = int(existing.rows_target) if isinstance(existing.rows_target, (int, float)) else 0
                print(f"[Retention] Resuming {job_type} job #{job_id} from checkpoint ticker={last_ticker!r}")
            else:
                total_source = 0
                total_target = 0
                job = RetentionJob(job_type=job_type, start_time=datetime.now())
                db.add(job)
                db.commit()
                job_id = job.id
        except Exception:
            db.rollback()
            job_id = None

        try:
            failed_tickers: list = []
            while True:
                ticker_batch = self._next_ticker_batch(rule, cutoff, last_ticker)
                if not ticker_batch:
                    break
                for ticker in ticker_batch:
                    # Cursor always advances (even on failure) so a persistently failing
                    # ticker cannot cause an infinite retry loop; it resumes next run.
                    last_ticker = ticker
                    time.sleep(0.01)  # Throttle CPU and yield execution between tickers
                    try:
                        src_n, tgt_n = self._process_ticker(db, rule, cutoff, ticker)
                    except Exception as e:
                        db.rollback()
                        print(f"[Retention] Ticker {ticker} ({rule.source_tf}->{rule.target_tf}) failed: {e}")
                        self._errors += 1
                        failed_tickers.append(ticker)
                        continue
                    total_source += src_n
                    total_target += tgt_n
                    self._checkpoint_job(db, job_id, last_ticker, total_source, total_target)

            # Preserve ticker-level failure info: a job with any ticker failures
            # must not be reported as COMPLETED — monitoring dashboards rely on
            # the status to detect per-ticker B-1 guard fires and other errors.
            if failed_tickers:
                summary = ", ".join(failed_tickers[:5])
                if len(failed_tickers) > 5:
                    summary += f" (+{len(failed_tickers) - 5} more)"
                final_status = "PARTIAL"
                error_msg = f"{len(failed_tickers)} ticker(s) failed: {summary}"
            else:
                final_status = "COMPLETED" if total_source > 0 else "SKIPPED"
                error_msg = None
            self._finish_job(db, job_id, final_status, total_source, total_target, error_msg)
            return total_source
        finally:
            db.close()

    def _next_ticker_batch(self, rule: RetentionRule, cutoff: datetime, last_ticker: Optional[str], batch_size: int = 500) -> List[str]:
        """Keyset-paginated scan of eligible source tickers (ticker > last)."""
        from models import Candle

        db = self._db_factory()
        try:
            query = db.query(Candle.ticker).filter(
                Candle.timeframe == rule.source_tf,
                Candle.timestamp < cutoff,
                Candle.is_completed == True,
            )
            if last_ticker:
                query = query.filter(Candle.ticker > last_ticker)
            rows = query.distinct().order_by(Candle.ticker.asc()).limit(batch_size).all()
            return [r[0] for r in rows]
        finally:
            db.close()

    @staticmethod
    def _target_bucket_tail(ts: datetime, source_tf: str, target_tf: str) -> Optional[datetime]:
        """Return the timestamp of the last source slot in ts's target bucket.

        For intraday chains (5m/15m/30m/1h source), this is the last NSE-session
        slot within the same target bucket, session-anchored at 09:15.

        For calendar chains (1D→1W, 1W→1M), this is the last calendar day within
        ts's bucket: Sunday for weekly buckets, month-end for monthly buckets.
        The DB query in _process_ticker uses Candle.timestamp <= tail_end to fetch
        remaining trading days/weeks within the same bucket before resampling,
        preventing a batch boundary from splitting a calendar bucket and producing
        a partial higher-timeframe candle.

        Example (1D→1W, ts = Wednesday 2026-07-01):
          week_monday = 2026-06-29; tail_end = 2026-07-05 (Sunday)
          Extension fetches Thu 2026-07-02 and Fri 2026-07-03 if they exist,
          so the resampler sees the complete Mon–Fri week.

        Example (1W→1M, ts = Monday 2021-08-23):
          month_start = 2021-08-01; tail_end = 2021-08-31
          Extension fetches Monday 2021-08-30 if it exists, so the resampler
          sees all August weeks before producing the August monthly candle.
        """
        # ── Calendar: 1D → 1W ───────────────────────────────────────────────
        # The resampler uses W-MON (Monday-anchored, closed='left').
        # Return Sunday of ts's week; the DB extension fetch collects the
        # remaining trading days (Tue–Fri, or whatever market days remain)
        # within the same ISO week, ensuring no partial week is resampled.
        if source_tf == "1D" and target_tf == "1W":
            week_monday = ts - timedelta(days=ts.weekday())
            return week_monday + timedelta(days=6)  # Sunday of that week

        # ── Calendar: 1W → 1M ───────────────────────────────────────────────
        # The resampler uses MS (Month Start, closed='left', label='left').
        # Weekly candles are labeled by their Monday timestamp; all Mondays
        # within a calendar month fall into that month's bucket.
        # Return the last day of ts's calendar month so the extension fetch
        # collects any remaining weekly candles in the same month.
        if source_tf == "1W" and target_tf == "1M":
            month_start = ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if month_start.month == 12:
                next_month = month_start.replace(year=month_start.year + 1, month=1)
            else:
                next_month = month_start.replace(month=month_start.month + 1)
            return next_month - timedelta(days=1)  # last day of current month

        # ── Intraday chains ──────────────────────────────────────────────────
        src_minutes = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(source_tf)
        tgt_minutes = {"15m": 15, "30m": 30, "1h": 60, "4h": 240}.get(target_tf)
        if src_minutes is None or tgt_minutes is None:
            return None
        session_open = ts.replace(hour=9, minute=15, second=0, microsecond=0)
        if ts < session_open:
            return None
        minutes_from_open = (ts - session_open).total_seconds() / 60
        bucket_index = int(minutes_from_open // tgt_minutes)
        bucket_start = session_open + timedelta(minutes=bucket_index * tgt_minutes)
        return bucket_start + timedelta(minutes=tgt_minutes - src_minutes)

    def _process_ticker(self, db, rule: RetentionRule, cutoff: datetime, ticker: str) -> tuple:
        from models import Candle
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        total_src = 0
        total_tgt = 0
        last_ts: Optional[datetime] = None

        while True:
            query = db.query(Candle).filter(
                Candle.ticker == ticker,
                Candle.timeframe == rule.source_tf,
                Candle.timestamp < cutoff,
                Candle.is_completed == True,
            )
            if last_ts is not None:
                query = query.filter(Candle.timestamp > last_ts)
            source_rows = query.order_by(Candle.timestamp.asc()).limit(rule.batch_size).all()
            if not source_rows:
                break

            # Prevent target-bucket split across batch iterations.
            # With batch_size=100 and 5m→15m: 100 % 3 == 1, so row 99 (0-indexed)
            # is always the FIRST slot of a new 15m bucket. The resampler would
            # produce a partial target candle; ON CONFLICT DO NOTHING then blocks
            # the correct complete candle when the remaining slots arrive next batch.
            # Fix: fetch the remaining slots of the last bucket before resampling.
            last_batch_ts = source_rows[-1].timestamp
            tail_end = RetentionService._target_bucket_tail(
                last_batch_ts, rule.source_tf, rule.target_tf
            )
            if tail_end is not None and last_batch_ts < tail_end:
                extra = db.query(Candle).filter(
                    Candle.ticker == ticker,
                    Candle.timeframe == rule.source_tf,
                    Candle.is_completed == True,
                    Candle.timestamp < cutoff,
                    Candle.timestamp > last_batch_ts,
                    Candle.timestamp <= tail_end,
                ).order_by(Candle.timestamp.asc()).all()
                if extra:
                    source_rows = list(source_rows) + extra

            source_dicts = [{
                "timestamp": r.timestamp,
                "open": r.open, "high": r.high, "low": r.low,
                "close": r.close, "volume": r.volume,
            } for r in source_rows]

            target_dicts = self._resample_svc.resample_5m_to(source_dicts, rule.target_tf)
            if not target_dicts:
                last_ts = source_rows[-1].timestamp
                continue

            if not self._verify_checksum(self._checksum(source_dicts), self._checksum(target_dicts)):
                raise ValueError(
                    f"CHECKSUM MISMATCH for {rule.source_tf}->{rule.target_tf} ticker {ticker}"
                )

            target_ts = []
            target_by_ts: Dict[datetime, Dict] = {}  # computed target, keyed by timestamp
            blocked_by_conflict: set = set()          # targets blocked by DO NOTHING

            for td in target_dicts:
                ts = td["timestamp"]
                if isinstance(ts, datetime):
                    insert_ts = ts
                elif isinstance(ts, (int, float)):
                    insert_ts = datetime.fromtimestamp(ts).replace(tzinfo=None)
                else:
                    continue

                target_ts.append(insert_ts)
                target_by_ts[insert_ts] = td
                stmt = pg_insert(Candle).values(
                    ticker=ticker,
                    timeframe=rule.target_tf,
                    timestamp=insert_ts,
                    open=td.get("open", 0),
                    high=td.get("high", 0),
                    low=td.get("low", 0),
                    close=td.get("close", 0),
                    volume=td.get("volume", 0),
                    is_completed=True,
                    data_source="RETENTION",
                    is_backfilled=False,
                ).on_conflict_do_nothing(constraint="uix_candle_key")
                result = db.execute(stmt)
                if result.rowcount == 0:
                    blocked_by_conflict.add(insert_ts)

            # B-1 partial-target guard: when DO NOTHING blocks an insert, the
            # existing candle at that timestamp may be a partial candle written by
            # a prior buggy run.  Compare its volume (an exact integer sum) with
            # the value we just computed from the current source batch.  A mismatch
            # means the existing candle is incomplete — deleting source rows would
            # permanently lose data that the target never captured.  Raise so
            # _downgrade logs the ticker as failed and the source rows are rolled
            # back; manual cleanup of the partial candle is required.
            if blocked_by_conflict:
                for blocked in sorted(blocked_by_conflict):
                    existing = db.query(Candle).filter(
                        Candle.ticker == ticker,
                        Candle.timeframe == rule.target_tf,
                        Candle.timestamp == blocked,
                    ).first()
                    expected_vol = int(target_by_ts[blocked].get("volume", 0))
                    if existing is None or existing.volume != expected_vol:
                        raise ValueError(
                            f"Partial target detected: {rule.target_tf} candle at {blocked} "
                            f"for {ticker} has volume={getattr(existing, 'volume', None)}, "
                            f"expected={expected_vol}. Source rows preserved. "
                            f"Manual cleanup of this candle is required."
                        )

            # Verify targets are actually present before touching source data.
            present = db.query(Candle.id).filter(
                Candle.ticker == ticker,
                Candle.timeframe == rule.target_tf,
                Candle.timestamp.in_(target_ts),
            ).count()
            if present != len(set(target_ts)):
                raise ValueError(
                    f"TARGET VERIFICATION FAILED: expected {len(set(target_ts))} target rows, found {present}"
                )

            source_ids = [r.id for r in source_rows]
            db.query(Candle).filter(Candle.id.in_(source_ids)).delete(synchronize_session=False)
            db.commit()

            total_src += len(source_rows)
            total_tgt += len(target_ts)
            # Read the cursor from the detached dict data (source_dicts) NOT from
            # the ORM row: after commit() the session expires all objects and the
            # source rows were just deleted, so touching source_rows[-1] raises
            # ObjectDeletedError.
            last_ts = source_dicts[-1]["timestamp"]
            if isinstance(last_ts, (int, float)):
                last_ts = datetime.fromtimestamp(last_ts).replace(tzinfo=None)

        return total_src, total_tgt

    # ── Job bookkeeping ─────────────────────────────────────────────────
    def _checkpoint_job(self, db, job_id, last_ticker, src, tgt):
        from models import RetentionJob
        from sqlalchemy import text
        try:
            db.execute(
                text("UPDATE retention_jobs SET last_ticker=:lt, rows_source=:rs, rows_target=:rt WHERE id=:jid"),
                {"lt": last_ticker, "rs": src, "rt": tgt, "jid": job_id},
            )
            db.commit()
        except Exception:
            db.rollback()

    def _finish_job(self, db, job_id, status, src, tgt, error=None):
        from sqlalchemy import text
        try:
            db.execute(
                text("UPDATE retention_jobs SET status=:st, rows_source=:rs, rows_target=:rt, end_time=:et, error_message=:em WHERE id=:jid"),
                {"st": status, "rs": src, "rt": tgt, "et": datetime.now(), "em": error, "jid": job_id},
            )
            db.commit()
        except Exception:
            db.rollback()

    # ── Checksum ────────────────────────────────────────────────────────
    def _checksum(self, candles: List[Dict]) -> Dict:
        if not candles:
            return {}
        opens = [c.get("open", 0) for c in candles]
        highs = [c.get("high", 0) for c in candles]
        lows = [c.get("low", 0) for c in candles]
        closes = [c.get("close", 0) for c in candles]
        volumes = [c.get("volume", 0) for c in candles]
        return {
            "count": len(candles),
            "open_first": opens[0],
            "close_last": closes[-1],
            "high_max": max(highs) if highs else 0,
            "low_min": min(lows) if lows else 0,
            "volume_sum": sum(volumes),
        }

    def _verify_checksum(self, source: Dict, target: Dict) -> bool:
        if not source or not target:
            return False
        return all([
            source["volume_sum"] == target["volume_sum"],
            source["open_first"] == target["open_first"],
            source["close_last"] == target["close_last"],
            source["high_max"] == target["high_max"],
            source["low_min"] == target["low_min"],
        ])

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "rows_converted": self._rows_converted,
            "errors": self._errors,
            "policy_rules": len(self.load_policy()),
        }