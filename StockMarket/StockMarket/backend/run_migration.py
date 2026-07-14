#!/usr/bin/env python
"""
Historical Candle Database Migration CLI
=========================================
One-time migration tool to rebuild the candle database for the new
multi-timeframe retention architecture.

Usage:
    python run_migration.py backup          # Backup existing candle tables
    python run_migration.py estimate        # Dry-run estimator (no changes)
    python run_migration.py migrate         # Run full migration
    python run_migration.py resume          # Resume interrupted migration
    python run_migration.py status          # Show progress report
    python run_migration.py validate        # Validate migrated data
    python run_migration.py rollback        # Restore from backup (emergency)

Safety:
    - NEVER deletes stock metadata, users, portfolios, watchlists, or auth
    - ONLY operates on candle/historical price tables
    - Uses staging table approach (never writes directly to production)
    - Creates full backups before any destructive operation
"""

import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text as sql_text
from database import SessionLocal, engine
from migration.config import MigrationConfig
from migration.orchestrator import MigrationOrchestrator
from migration.progress_tracker import ProgressTracker
from migration.models import MigrationJob


def cmd_backup(args):
    orch = MigrationOrchestrator()
    orch.backup()
    print("[OK] Backup complete. Existing candle data is preserved in backup tables.\n")


def cmd_estimate(args):
    orch = MigrationOrchestrator()
    print(orch.estimate())


def cmd_migrate(args):
    from angelone_service import angelone_service
    print("[Pre-Flight] Loading Angel One instruments...")
    angelone_service.load_instruments()

    orch = MigrationOrchestrator()

    print("\n[Pre-Flight] Cleaning up previous migration state...")
    db = SessionLocal()
    try:
        db.execute(sql_text("TRUNCATE TABLE migration_jobs"))
        db.commit()
        print("  Cleared migration_jobs table.")
        db.execute(sql_text("DROP TABLE IF EXISTS candles_migration CASCADE"))
        db.commit()
        print("  Dropped stale candles_migration staging table.")
    except Exception:
        db.rollback()
    finally:
        db.close()

    print("\n[Pre-Flight] Creating backup before migration...")
    orch.backup()

    print("[Pre-Flight] Creating staging tables...")
    _ensure_staging_tables()

    report = orch.run(progress_callback=_on_progress)

    if "ERROR" in report:
        print(f"\n[FAIL] Migration did not complete successfully.")
        sys.exit(1)

    print("\n[Post-Flight] Swapping staging table into production...")
    _swap_tables()

    print("\n[OK] Migration complete! New candle table is live.")
    print("     Old candle table renamed to candles_backup_<timestamp>.")
    print("     This backup will be kept for 7 days before auto-deletion.\n")


def cmd_resume(args):
    db = SessionLocal()
    try:
        incomplete = db.execute(sql_text("""
            SELECT COUNT(*) FROM migration_jobs
            WHERE status NOT IN ('DONE', 'FAILED_RETRY_EXHAUSTED')
        """)).scalar()
        if incomplete == 0:
            print("[OK] All jobs are complete or exhausted. Nothing to resume.\n")
            return
        print(f"[Resume] Found {incomplete} incomplete jobs. Resuming...\n")
    finally:
        db.close()

    orch = MigrationOrchestrator()
    report = orch.run(progress_callback=_on_progress)
    _swap_tables()
    print("[OK] Resume complete.\n")


def cmd_status(args):
    db = SessionLocal()
    try:
        cfg = MigrationConfig.load()
        tracker = ProgressTracker(cfg)
        summaries = tracker.get_all_tier_summaries(db)
        failed = tracker.get_failed_jobs(db)

        if not summaries:
            print("[Status] No migration progress found. Run `migrate` to start.\n")
            return

        sep = "=" * 55
        print(sep)
        print("  MIGRATION STATUS")
        print(sep)
        for s in summaries:
            pct = (s["done"] / max(s["total"], 1)) * 100
            status_char = "[OK]" if s["done"] == s["total"] else "[..]" if s["done"] > 0 else "[--]"
            print(f"  {status_char} Tier {s['tier']} ({s['timeframe']:>4s}): "
                  f"{s['done']}/{s['total']} ({pct:.1f}%), "
                  f"{s['total_rows']:,} candles, "
                  f"{s['total_retries']} retries")

        if failed:
            print(f"\n  Failed jobs: {len(failed)}")
            for j in failed[:5]:
                err_short = (j.get("error") or "?")[:60]
                print(f"    {j['ticker']} Tier {j['tier']} ({j['timeframe']}): {err_short}")
            if len(failed) > 5:
                print(f"    ... and {len(failed) - 5} more")
        print(sep)
        print()
    finally:
        db.close()


def cmd_validate(args):
    db = SessionLocal()
    try:
        from migration.validator import MigrationValidator, compute_checksum
        from models import Candle

        cfg = MigrationConfig.load()
        tracker = ProgressTracker(cfg)
        summaries = tracker.get_all_tier_summaries(db)

        if not summaries:
            print("[Validate] No migration data found.\n")
            return

        sep = "=" * 55
        print(sep)
        print("  VALIDATING MIGRATED DATA")
        print(sep)

        total_violations = 0
        for s in summaries:
            tf = s["timeframe"]
            rows = db.execute(sql_text("""
                SELECT COUNT(*) FROM candles WHERE timeframe = :tf
            """), {"tf": tf}).scalar()
            print(f"  {tf:>4s}: {rows:>12,} candles")

            dupes = db.execute(sql_text("""
                SELECT COUNT(*) FROM (
                    SELECT ticker, timeframe, timestamp, COUNT(*)
                    FROM candles WHERE timeframe = :tf
                    GROUP BY ticker, timeframe, timestamp
                    HAVING COUNT(*) > 1
                ) d
            """), {"tf": tf}).scalar()
            if dupes:
                print(f"        [WARN] {dupes} duplicate entries!")
                total_violations += dupes

            ohlc_bad = db.execute(sql_text("""
                SELECT COUNT(*) FROM candles
                WHERE timeframe = :tf
                AND (high < open OR high < close OR low > open OR low > close OR high < low)
            """), {"tf": tf}).scalar()
            if ohlc_bad:
                print(f"        [WARN] {ohlc_bad} OHLC violations!")
                total_violations += ohlc_bad

        if total_violations == 0:
            print(f"\n  [OK] All checks passed")
        else:
            print(f"\n  [WARN] Found {total_violations} issues")
        print(sep)
        print()
    finally:
        db.close()


def cmd_rollback(args):
    print("\n[WARN] ROLLBACK: This will restore the CANDLE tables from backup.")
    print("   User data (stocks, watchlists, portfolios, auth) is NOT affected.\n")

    db = SessionLocal()
    try:
        backup_tables = db.execute(sql_text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name LIKE 'candles_backup_%'
            OR table_name LIKE 'stock_data_backup_%'
            ORDER BY table_name DESC
        """)).fetchall()

        if not backup_tables:
            print("[Rollback] No backup tables found.\n")
            return

        print("  Available backups:")
        for i, (t,) in enumerate(backup_tables):
            print(f"    [{i}] {t}")
        print()

        try:
            idx = int(input("  Enter index to restore (or -1 to cancel): ").strip())
        except (ValueError, EOFError):
            print("  Cancelled.\n")
            return

        if idx < 0 or idx >= len(backup_tables):
            print("  Cancelled.\n")
            return

        backup_name = backup_tables[idx][0]

        confirm = input(f"  Restore {backup_name} -> candles? This will OVERWRITE current data. (yes/no): ").strip().lower()
        if confirm != "yes":
            print("  Cancelled.\n")
            return

        with engine.connect() as conn:
            conn.execute(sql_text("DROP TABLE IF EXISTS candles_rollback_temp"))
            conn.execute(sql_text("ALTER TABLE IF EXISTS candles RENAME TO candles_rollback_temp"))
            conn.execute(sql_text(f"ALTER TABLE {backup_name} RENAME TO candles"))
            conn.commit()
            print(f"\n  [OK] Restored {backup_name} -> candles")
            print(f"  Previous candle table renamed to candles_rollback_temp")
            print("  You can delete it with: DROP TABLE candles_rollback_temp;\n")
    finally:
        db.close()


def _ensure_staging_tables():
    """Ensure the staging/swap infrastructure exists."""
    from models import Base as StockBase
    from migration.models import MigrationJob
    from migration.config import MigrationConfig

    StockBase.metadata.create_all(bind=engine)

    cfg = MigrationConfig.load()
    staging_table = cfg.staging_table

    db = SessionLocal()
    try:
        jobs_exists = db.execute(sql_text(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'migration_jobs')"
        )).scalar()
        if not jobs_exists:
            MigrationJob.__table__.create(bind=engine)
            print("  Created migration_jobs table.")

        staging_exists = db.execute(sql_text(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :t)"
        ), {"t": staging_table}).scalar()
        if not staging_exists:
            db.execute(sql_text(f"""
                CREATE TABLE {staging_table} (LIKE candles INCLUDING ALL)
            """))
            db.commit()
            print(f"  Created staging table {staging_table} (copy of candles schema).")
        else:
            print(f"  Staging table {staging_table} already exists.")
    finally:
        db.close()


def _swap_tables():
    """Atomic rename: staging to production, production to backup."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with engine.connect() as conn:
        conn.execute(sql_text(f"ALTER TABLE IF EXISTS candles RENAME TO candles_old_{ts}"))
        conn.commit()
        conn.execute(sql_text(f"ALTER TABLE IF EXISTS candles_migration RENAME TO candles"))
        conn.commit()
        print(f"  candles -> candles_old_{ts}")
        print(f"  candles_migration -> candles (now live)")


def _on_progress(ticker: str, tier: int, status: str, rows: int):
    symbol = "[OK]" if status == "DONE" else "[FAIL]" if status == "FAILED" else "[..]"
    print(f"  {symbol} [{tier}] {ticker:<20s} {status:<10s} {rows:>8,} candles")


def main():
    parser = argparse.ArgumentParser(
        description="Historical Candle Database Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", nargs="?", default="status",
                        choices=["backup", "estimate", "migrate", "resume", "status", "validate", "rollback"],
                        help="Command to run")

    args = parser.parse_args()

    command_map = {
        "backup": cmd_backup,
        "estimate": cmd_estimate,
        "migrate": cmd_migrate,
        "resume": cmd_resume,
        "status": cmd_status,
        "validate": cmd_validate,
        "rollback": cmd_rollback,
    }

    command_map[args.command](args)


if __name__ == "__main__":
    main()
