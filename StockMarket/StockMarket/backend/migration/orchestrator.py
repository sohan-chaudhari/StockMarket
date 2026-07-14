import time
import threading
from datetime import datetime
from typing import List, Optional, Callable
from sqlalchemy import text as sql_text

from database import SessionLocal, engine
from migration.config import MigrationConfig, TierConfig, compute_tier_date_ranges, estimate_candles_per_ticker
from migration.batch_downloader import BatchDownloader
from migration.progress_tracker import ProgressTracker
from migration.report import generate_report, format_bytes


class MigrationOrchestrator:
    def __init__(self, cfg: Optional[MigrationConfig] = None):
        self._cfg = cfg or MigrationConfig.load()
        self._tracker = ProgressTracker(self._cfg)
        self._start_time: Optional[datetime] = None
        self._stats_lock = threading.Lock()
        self._all_stats = {}

    def estimate(self) -> str:
        lines = []
        sep = "=" * 55
        lines.append(sep)
        lines.append("  MIGRATION ESTIMATOR")
        lines.append(sep)
        lines.append("")

        ticker_count = self._count_tickers()
        tier_ranges = compute_tier_date_ranges(self._cfg)

        lines.append(f"  Total tickers:    {ticker_count:,}")
        lines.append(f"  Total tiers:      {len(self._cfg.tiers)}")
        total_api_calls = ticker_count * len(self._cfg.tiers)
        lines.append(f"  Total API calls:  ~{total_api_calls:,}")
        lines.append("")

        lines.append("  ESTIMATED CANDLES PER TIER:")
        total_est = 0
        for tier in self._cfg.tiers:
            count = ticker_count * estimate_candles_per_ticker(tier)
            dr = tier_ranges.get(tier.timeframe)
            dr_str = f" (no range)" if dr is None else f" ({dr[0]} to {dr[1]})" if dr else f" (all history)"
            lines.append(f"    {tier.timeframe:>4s}:  {count:>12,}{dr_str}")
            total_est += count
        lines.append(f"    {'-' * 25}")
        lines.append(f"    Total: {total_est:>15,}")
        lines.append("")

        network_time = total_api_calls / (self._cfg.requests_per_second * self._cfg.workers)
        insert_time = total_est / 50000
        resample_time = total_est * 0.00001 / self._cfg.workers
        total_hours = (network_time + insert_time + resample_time) / 3600

        lines.append("  ESTIMATED RUNTIME:")
        lines.append(f"    API calls:        {total_api_calls:,}")
        lines.append(f"    Rate limit:       {self._cfg.requests_per_second} req/sec")
        lines.append(f"    Workers:          {self._cfg.workers}")
        lines.append(f"    Network time:     ~{network_time / 3600:.1f}h")
        lines.append(f"    DB insert time:   ~{insert_time / 3600:.1f}h")
        lines.append(f"    Resample time:    ~{resample_time / 3600:.1f}h")
        lines.append(f"    {'-' * 25}")
        lines.append(f"    Total:            ~{total_hours:.1f}h")
        lines.append("")

        bytes_per_candle = 120
        total_bytes = total_est * bytes_per_candle
        lines.append("  ESTIMATED STORAGE:")
        lines.append(f"    Per candle:       ~{bytes_per_candle} bytes")
        lines.append(f"    Data only:        ~{format_bytes(total_bytes)}")
        lines.append(f"    With indexes:     ~{format_bytes(int(total_bytes * 2.5))}")
        lines.append("")

        lines.append(sep)
        return "\n".join(lines)

    def backup(self) -> bool:
        print("\n[Backup] Creating backup of existing candle tables...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tables = ["candles", "stock_data", "intraday_candles_5min", "intraday_candles_15min",
                   "intraday_candles_1min", "current_day_candle", "intraday_ticks"]
        with engine.connect() as conn:
            for table in tables:
                result = conn.execute(sql_text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :t)"
                ), {"t": table}).scalar()
                if result:
                    backup_name = f"{table}_backup_{ts}"
                    print(f"  Backing up {table} -> {backup_name}")
                    conn.execute(sql_text(f"CREATE TABLE {backup_name} AS SELECT * FROM {table}"))
                    conn.commit()
                    print(f"  Done.")
                else:
                    print(f"  Skipping {table} (does not exist)")
        print("[Backup] Complete.\n")
        return True

    def run(self, progress_callback: Optional[Callable] = None) -> str:
        self._start_time = datetime.now()
        print(f"\n{'='*60}")
        print(f"  MIGRATION STARTED at {self._start_time.strftime('%Y-%m-%d %H:%M:%S')} IST")
        print(f"{'='*60}\n")

        all_tickers = self._get_all_tickers()
        if not all_tickers:
            return "ERROR: No tickers found in stock_metadata"

        print(f"Found {len(all_tickers)} tickers.\n")

        for tier_index, tier_cfg in enumerate(self._cfg.tiers, start=1):
            downloader = BatchDownloader(self._cfg)
            downloader.run_tier(tier_index, tier_cfg, all_tickers, progress_callback)
            self._all_stats[f"tier_{tier_index}"] = downloader.get_stats()

            db = SessionLocal()
            try:
                summary = self._tracker.get_tier_summary(db, tier_index)
                pct = (summary["done"] / max(summary["total"], 1)) * 100
                print(f"\n  Tier {tier_index} ({tier_cfg.timeframe}): "
                      f"{summary['done']}/{summary['total']} done ({pct:.1f}%), "
                      f"{summary['total_rows']:,} candles\n")
            finally:
                db.close()

        return self._generate_final_report(all_tickers)

    def _generate_final_report(self, all_tickers: List[str]) -> str:
        db = SessionLocal()
        try:
            tier_summaries = self._tracker.get_all_tier_summaries(db)
            failed_jobs = self._tracker.get_failed_jobs(db)

            db_size = None
            try:
                row = db.execute(sql_text(
                    "SELECT pg_size_pretty(pg_total_relation_size('candles'))"
                )).scalar()
                db_size = row
            except Exception:
                pass

            elapsed = datetime.now() - self._start_time
            hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            elapsed_str = f"{hours}h {minutes}m {seconds}s"

            report = generate_report(
                start_time=self._start_time,
                end_time=datetime.now(),
                tier_summaries=tier_summaries,
                failed_jobs=failed_jobs,
                stats=self._all_stats,
                total_tickers=len(all_tickers),
                elapsed_formatted=elapsed_str,
            )

            print("\n" + report + "\n")
            return report
        finally:
            db.close()

    def _count_tickers(self) -> int:
        db = SessionLocal()
        try:
            return db.execute(sql_text("SELECT COUNT(*) FROM stock_metadata")).scalar() or 0
        finally:
            db.close()

    def _get_all_tickers(self) -> List[str]:
        db = SessionLocal()
        try:
            rows = db.execute(
                sql_text("SELECT DISTINCT ticker FROM stock_metadata WHERE is_active = TRUE ORDER BY ticker")
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            db.close()
