#!/usr/bin/env python
"""
Scoped 1D-only historical migration entrypoint.
======================================================================
Dedicated alternative to run_migration.py's `migrate` command. That command
(cmd_migrate -> MigrationOrchestrator.run() -> _swap_tables()) processes
ALL 8 configured tiers and finishes by renaming `candles` away and renaming
`candles_migration` into its place -- a full-table replacement that would
discard every other timeframe's live production data. This script NEVER
calls cmd_migrate, MigrationOrchestrator.run(), or _swap_tables(), and never
renames, drops, or truncates production `candles`. It processes ONLY
timeframe='1D', using the already-approved pieces:
    migration.identity        (NSE-priority ticker/exchange resolver)
    migration.plan_1d         (pure 730-calendar-day range planning)
    migration.config          (chunk_date_range, compute_1d_required_range,
                                get_1d_retention_days -- single source of
                                truth is config/retention_policy.py, never
                                a hardcoded 730 here)
    migration.batch_downloader.BatchDownloader.run_tier()
                               (reused directly for ONLY the 1D tier --
                                bypasses MigrationOrchestrator.run()'s
                                all-tier loop entirely; still gets the
                                existing rate limiter, retry/backoff,
                                checksum validation, and the per-ticker 1D
                                gap-narrowing already wired into
                                _worker_loop for tier_cfg.timeframe=="1D")
    migration.staging          (refuses to silently reuse an incompatible
                                or stale staging table)
    migration.promotion        (additive, idempotent staging -> candles)

Usage:
    python run_1d_migration.py --dry-run
        Discovery + range calculation ONLY. Zero Angel One calls (does not
        call angelone_service.load_instruments(), which is the only thing
        in the token-lookup path that can make a network request -- see
        _dry_run_token_check()). Zero database writes. Safe to run anytime.

    python run_1d_migration.py
        Same as --dry-run (the safe default when no flag is given at all).

    python run_1d_migration.py --execute-1d-migration
        The real run: resolve universe -> prepare staging -> fetch missing
        1D ranges from Angel One -> validate -> promote additively ->
        reconcile -> report. Refuses to do anything real without this exact
        flag.

    python run_1d_migration.py --execute-1d-migration --tickers RELIANCE,TCS,INFY
        Same real run, scoped to only the listed tickers (case-insensitive)
        -- e.g. for a small controlled test before running the full
        universe. Applies to --dry-run too. Filtering happens before
        identity resolution, so a scoped run's plan, fetch, and promotion
        all only ever see the requested tickers.
"""

import argparse
import os
import sys
from datetime import date
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ONE_D_TIER_INDEX = 6  # matches "1D"'s position in migration.yaml's tier list
                       # (5m,15m,30m,1h,4h,1D,...) for a full run, so
                       # migration_jobs rows from this scoped script read
                       # coherently if ever inspected alongside a full run.
                       # The exact number has no other significance -- it
                       # only needs to be stable across runs of this script.


def _resolve_active_identity_rows(db):
    from sqlalchemy import text
    rows = db.execute(text("SELECT ticker, exchange FROM stock_metadata WHERE is_active = TRUE")).fetchall()
    return [{"ticker": r[0], "exchange": r[1]} for r in rows]


def _resolve_named_identity_rows(db, tickers: List[str]):
    """Identity rows for an EXPLICITLY NAMED ticker list, ignoring is_active.

    The default path deliberately restricts the universe to
    is_active = TRUE. That flag turned out to be an unreliable description
    of what is tradeable -- it reflects which tickers survived a 2026-07-03
    ingestion cutover, so securities that demonstrably trade today (e.g.
    HDFCLIFE, TRENT, SBILIFE) are marked inactive. Because --tickers was
    applied as an INTERSECTION with the active set, those tickers could not
    be migrated even when named outright.

    This function is reachable only when the caller passes an explicit list
    AND --include-inactive, so naming a ticker is itself the authorization.
    It never widens the default universe. Tickers absent from
    stock_metadata entirely have no exchange to resolve and are returned by
    the caller as unresolved rather than guessed at."""
    from sqlalchemy import text
    wanted = sorted({t.strip().upper() for t in tickers if t.strip()})
    if not wanted:
        return []
    rows = db.execute(
        text("SELECT ticker, exchange FROM stock_metadata WHERE UPPER(ticker) = ANY(:tks)"),
        {"tks": wanted},
    ).fetchall()
    return [{"ticker": r[0], "exchange": r[1]} for r in rows]


def _read_tickers_file(path: str) -> List[str]:
    """One ticker per line; blank lines and #-comments ignored. Needed
    because a 2,400-ticker --tickers argument would exceed the Windows
    command-line length limit."""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def _dry_run_token_check(ticker: str, exchange: str) -> bool:
    """Zero-network token resolvability check for the dry-run report.
    Deliberately does NOT call angelone_service.load_instruments() (the only
    call in this path capable of triggering a network request, to download
    the instrument master when no local cache is present) -- get_token()
    itself never makes a network call on its own; with no instrument list
    loaded it just checks the ~2400-symbol hardcoded table and returns None
    for anything else, which is exactly the "cannot verify without a
    download" signal this check is meant to surface, not hide."""
    from angelone_service import angelone_service
    return angelone_service.get_token(ticker, exchange) is not None


def build_plan(ticker_filter: Optional[List[str]] = None, include_inactive: bool = False,
               retention_days_override: Optional[int] = None):
    """Shared by both dry-run and the real run's planning step. Read-only.

    ticker_filter: optional explicit list of ticker strings to scope the
    ENTIRE plan to (case-insensitive) -- e.g. for a small controlled
    execution test. Filtering happens before identity resolution, so a
    scoped run's plan, fetch, and (later) promotion all only ever see the
    requested tickers; nothing about the rest of the universe is touched
    or even queried beyond the initial stock_metadata read.

    retention_days_override: when given (--deep-backfill-days), the plan's
    required_start is computed from THIS number instead of the real 730-day
    retention policy (migration.yaml is never touched). compute_1d_required_range()
    still narrows to only the OLDER gap missing before each ticker's existing
    earliest row -- the already-fetched 2yr window is never re-requested."""
    from database import SessionLocal
    from migration.config import MigrationConfig, get_1d_retention_days
    from migration.plan_1d import build_1d_migration_plan, audit_existing_1d_coverage

    db = SessionLocal()
    try:
        if ticker_filter and include_inactive:
            # Explicitly named tickers authorize themselves -- see
            # _resolve_named_identity_rows for why is_active is not a
            # trustworthy universe filter.
            identity_rows = _resolve_named_identity_rows(db, ticker_filter)
        else:
            identity_rows = _resolve_active_identity_rows(db)
            if ticker_filter:
                wanted = {t.strip().upper() for t in ticker_filter if t.strip()}
                identity_rows = [r for r in identity_rows if r["ticker"].upper() in wanted]
        tickers = [r["ticker"] for r in identity_rows]
        existing = audit_existing_1d_coverage(db, tickers)
    finally:
        db.close()

    cfg = MigrationConfig.load()
    retention_days = retention_days_override if retention_days_override is not None else get_1d_retention_days()
    plan = build_1d_migration_plan(
        identity_rows=identity_rows,
        existing_1d_start_by_ticker=existing,
        today=date.today(),
        retention_days=retention_days,
        max_chunk_days=_one_d_chunk_days(cfg),
        token_resolver=_dry_run_token_check,
    )
    return plan, cfg


def _one_d_chunk_days(cfg) -> int:
    """Chunk width the 1D tier will actually use at execution time.

    The planner must chunk exactly the way the downloader will, or the
    dry-run's request count is a fiction. Both now resolve the width from
    the SAME place: the 1D TierConfig's per-tier override
    (migration.yaml -> max_date_range_days: 730), falling back to the global
    default only if that tier or override is absent."""
    tier = next((t for t in cfg.tiers if t.timeframe == "1D"), None)
    if tier is None:
        return cfg.max_date_range_days
    return tier.effective_max_date_range_days(cfg.max_date_range_days)


def print_dry_run_report(plan, cfg):
    sep = "=" * 62
    print(sep)
    print("  1D MIGRATION -- DRY RUN (0 Angel One calls, 0 writes)")
    print(sep)
    print(f"  Retention policy source: config/retention_policy.py (1D->1W after_days)")
    print(f"  Retention window:        {plan.retention_days} calendar days")
    print(f"  Today:                   {plan.today}")
    print(f"  Required start date:     {plan.required_start}")
    print()
    print(f"  Active canonical tickers:            {len(plan.identities):,}")
    print(f"  NSE/BSE shadowed listings excluded:  {len(plan.shadowed):,}")
    if plan.shadowed:
        for s in plan.shadowed[:10]:
            print(f"      {s.ticker}: {s.excluded_exchange} shadowed by {s.canonical_exchange}")
        if len(plan.shadowed) > 10:
            print(f"      ... and {len(plan.shadowed) - 10} more")
    print()
    print(f"  No existing 1D history:              {len(plan.no_history_tickers):,}")
    print(f"  Partial 1D history (gap to fill):     {len(plan.partial_history_tickers):,}")
    print(f"  Already satisfy {plan.retention_days}-day requirement:   {len(plan.complete_tickers):,}")
    print()
    unresolved = plan.unresolvable_token_tickers
    print(f"  Cannot resolve Angel One token:       {len(unresolved):,}"
          f"{'  (checked against hardcoded/cached tokens only -- no network call made)' if unresolved else ''}")
    if unresolved:
        for p in unresolved[:10]:
            print(f"      {p.identity.ticker} ({p.identity.exchange})")
        if len(unresolved) > 10:
            print(f"      ... and {len(unresolved) - 10} more")
    print()

    if plan.partial_history_tickers or plan.no_history_tickers:
        sample = (plan.partial_history_tickers + plan.no_history_tickers)[0]
        print(f"  Example range calculation ({sample.identity.ticker}):")
        print(f"      existing_start = {sample.existing_start}")
        print(f"      fetch_range    = {sample.fetch_range}")
        print(f"      chunks (<= {_one_d_chunk_days(cfg)}d each) = {len(sample.chunks)}")
        print()

    print(f"  Total required chunks (<= {_one_d_chunk_days(cfg)}d each): {plan.total_chunks:,}")
    print(f"  Estimated Angel One requests:         {plan.estimate_requests():,}")
    runtime_s = plan.estimate_runtime_seconds(cfg.requests_per_second, cfg.workers)
    print(f"  Estimated runtime @ {cfg.requests_per_second} req/s GLOBAL "
          f"({cfg.workers} workers share one rate limiter, so they do NOT multiply throughput): "
          f"~{runtime_s / 3600:.2f}h ({runtime_s / 60:.0f} min)")
    print(f"  Total expected 1D rows (upper bound, calendar-day count): {plan.total_expected_rows:,}")
    print()

    anomalies = [p for p in plan.ticker_plans if p.needs_fetch and p.fetch_range[0] > p.fetch_range[1]]
    print(f"  Range calculation anomalies:          {len(anomalies):,}")
    for p in anomalies[:10]:
        print(f"      {p.identity.ticker}: {p.fetch_range}")

    print(sep)
    print("  No Angel One calls made. No database rows written. Nothing was staged or promoted.")
    print(sep)


def _parse_ticker_filter(args) -> Optional[List[str]]:
    path = getattr(args, "tickers_file", None)
    if path:
        return _read_tickers_file(path)
    raw = getattr(args, "tickers", None)
    if not raw:
        return None
    return [t for t in raw.split(",") if t.strip()]


def cmd_dry_run(args):
    plan, cfg = build_plan(ticker_filter=_parse_ticker_filter(args),
                            include_inactive=getattr(args, "include_inactive", False),
                            retention_days_override=getattr(args, "deep_backfill_days", None))
    print_dry_run_report(plan, cfg)


def _run_completeness_check(plan, all_identities):
    """Post-promotion completeness gate. Checks every ticker that was part
    of this run (not just the ones that needed fetching -- a ticker that
    'already satisfied' the retention window is exactly the case where a
    stale/gapped history could otherwise go unchecked forever) against the
    full [required_start, today] window, using migration.completeness
    (exchange_calendar.nse_calendar-based, weekend/holiday aware).

    Outcome per ticker:
      * complete                    -> left DONE
      * incomplete, fetch FAILED    -> stays/becomes FAILED (retryable): we
                                       never got a clean answer, so the gap
                                       is not proven unfillable
      * incomplete, fetch SUCCEEDED -> KNOWN_ABSENCES: every
                                       requested chunk returned a SUCCESSFUL
                                       response and Angel One simply has no
                                       candle for those sessions. Retrying
                                       would re-request the identical range
                                       and get the identical empty answer
                                       forever.

    The distinction rests entirely on whether the fetch itself succeeded --
    which the post-fix fetch layer now reports truthfully (a rate-limit or
    network error can no longer masquerade as a successful empty response).
    That is what makes this policy safe: it can only be reached on evidence,
    never on an assumption that a gap is 'probably fine'.

    Returns {ticker: CompletenessResult}."""
    from database import SessionLocal
    from migration.completeness import audit_completeness
    from migration.progress_tracker import ProgressTracker
    from migration.models import MigrationJob
    from migration.config import MigrationConfig

    db = SessionLocal()
    try:
        targets = [(i.ticker, plan.required_start, plan.today) for i in all_identities]
        results = audit_completeness(db, targets, timeframe="1D")

        tracker = ProgressTracker(MigrationConfig.load())
        for ticker, result in results.items():
            if result.passed:
                continue
            job = db.query(MigrationJob).filter(
                MigrationJob.ticker == ticker, MigrationJob.tier == ONE_D_TIER_INDEX,
            ).first()
            if job is None:
                continue
            # A job still reading DONE here means every chunk fetched cleanly.
            fetch_succeeded = job.status in tracker.TERMINAL_STATUSES
            if fetch_succeeded:
                tracker.mark_complete_with_known_absences(
                    db, job,
                    f"Known absences (fetch succeeded, Angel One returned no candle): {result.summary()}",
                )
            else:
                tracker.mark_failed(db, job, f"Completeness check failed: {result.summary()}")
        db.commit()
        return results
    finally:
        db.close()


def _print_completeness_report(results: dict, classify: bool = True):
    """Prints raw completeness plus, when `classify` is set, an
    evidence-based breakdown of WHY sessions are missing (see
    migration.completeness.classify_result): a date no ticker anywhere has
    is a market-wide closure; a date the rest of the market traded is a real
    gap. The raw missing count is always printed unchanged -- classification
    explains, it never rewrites the measurement."""
    failed = {t: r for t, r in results.items() if not r.passed}
    print()
    print(f"[1D Migration] Completeness check: {len(results) - len(failed)}/{len(results)} tickers complete.")

    classifications = {}
    if classify and failed:
        from database import SessionLocal
        from migration.completeness import classify_result
        db = SessionLocal()
        try:
            for ticker, r in failed.items():
                classifications[ticker] = classify_result(db, r)
        finally:
            db.close()

    if failed:
        print(f"[1D Migration] {len(failed)} ticker(s) FAILED completeness -- downgraded to FAILED, "
              f"NOT considered successfully migrated:")
        for ticker, r in list(failed.items())[:20]:
            c = classifications.get(ticker)
            if c:
                print(f"      {ticker}: {r.summary()}")
                print(f"          -> market-closure(explained): {c['market_closure']}   "
                      f"security-absent(REAL): {c['security_absent']}   "
                      f"indeterminate: {c['indeterminate']}   "
                      f"UNEXPLAINED: {c['unexplained']}")
                unexplained_dates = [str(d) for d, (cause, _cov) in sorted(c["detail"].items())
                                     if cause.value != "market_closure"]
                if unexplained_dates:
                    shown = ", ".join(unexplained_dates[:12])
                    more = f" (+{len(unexplained_dates) - 12} more)" if len(unexplained_dates) > 12 else ""
                    print(f"          unexplained dates: {shown}{more}")
            else:
                print(f"      {ticker}: {r.summary()}")
                for g in r.gaps[:5]:
                    print(f"          gap: {g.start} .. {g.end} ({g.missing_sessions} session(s))")
        if len(failed) > 20:
            print(f"      ... and {len(failed) - 20} more")

        if classifications:
            tot_unexplained = sum(c["unexplained"] for c in classifications.values())
            tot_closure = sum(c["market_closure"] for c in classifications.values())
            clean = [t for t, c in classifications.items() if c["unexplained"] == 0]
            print()
            print(f"[1D Migration] Aggregate: {tot_closure} market-closure session(s) (explained), "
                  f"{tot_unexplained} UNEXPLAINED session(s).")
            print(f"[1D Migration] {len(clean)}/{len(classifications)} ticker(s) have ZERO unexplained gaps "
                  f"(their only absences are market-wide closures).")


def cmd_execute(args):
    """The real run. Only reached with --execute-1d-migration. Never calls
    cmd_migrate / MigrationOrchestrator.run() / _swap_tables(); never
    renames, drops, or truncates `candles`."""
    from database import SessionLocal
    from migration.config import MigrationConfig
    from migration.staging import prepare_staging_table
    from migration.batch_downloader import BatchDownloader
    from migration.promotion import promote_1d_candles, reconcile_1d_promotion
    from angelone_service import angelone_service

    ticker_filter = _parse_ticker_filter(args)
    deep_backfill_days = getattr(args, "deep_backfill_days", None)
    print("[1D Migration] Planning (read-only)...")
    plan, cfg = build_plan(ticker_filter=ticker_filter,
                            include_inactive=getattr(args, "include_inactive", False),
                            retention_days_override=deep_backfill_days)
    if deep_backfill_days is not None:
        print(f"[1D Migration] DEEP BACKFILL MODE: required_start={plan.required_start} "
              f"({deep_backfill_days}d back) -- this run's fetch target only, the real "
              f"730-day retention/compression policy in migration.yaml is unchanged.")
    if ticker_filter:
        print(f"[1D Migration] Scoped to {len(plan.identities)} explicitly requested ticker(s): "
              f"{[i.ticker for i in plan.identities]}")
    to_fetch = [p for p in plan.ticker_plans if p.needs_fetch]
    print(f"[1D Migration] {len(to_fetch)} of {len(plan.identities)} tickers need fetching "
          f"({plan.total_chunks} chunks, ~{plan.estimate_requests()} requests).")

    print("[1D Migration] Loading Angel One instrument list (may download if no local cache)...")
    angelone_service.load_instruments()

    db = SessionLocal()
    try:
        print("[1D Migration] Preparing staging table...")
        status = prepare_staging_table(db, cfg.staging_table)
        print(f"[1D Migration] Staging table: {status}")
    finally:
        db.close()

    tier_cfg = next((t for t in cfg.tiers if t.timeframe == "1D"), None)
    if tier_cfg is None:
        print("[1D Migration] ERROR: no '1D' tier found in migration.yaml -- aborting.")
        sys.exit(1)

    identities_to_fetch = [p.identity for p in to_fetch]
    if identities_to_fetch:
        downloader = BatchDownloader(cfg)
        print(f"[1D Migration] Fetching {len(identities_to_fetch)} tickers (tier index {ONE_D_TIER_INDEX})...")
        downloader.run_tier(ONE_D_TIER_INDEX, tier_cfg, identities_to_fetch,
                             required_start_override=plan.required_start if deep_backfill_days is not None else None)
        print(f"[1D Migration] Fetch stats: {downloader.get_stats()}")
    else:
        print("[1D Migration] No tickers require fetching -- all satisfy the retention window already.")

    db = SessionLocal()
    try:
        print("[1D Migration] Promoting staged 1D rows into production `candles` (additive only)...")
        promo_result = promote_1d_candles(db, staging_table=cfg.staging_table)
        print(f"[1D Migration] Promotion result: {promo_result}")

        print("[1D Migration] Reconciling staging vs. production...")
        recon = reconcile_1d_promotion(db, staging_table=cfg.staging_table)
        print(f"[1D Migration] Reconciliation: {recon}")
    finally:
        db.close()

    completeness_results = _run_completeness_check(plan, plan.identities)
    _print_completeness_report(completeness_results)

    print("[1D Migration] Done. Staging table left in place as an audit trail "
          "(not dropped -- clean it up explicitly if/when you want to).")


def cmd_repair_gaps(args):
    """Targeted additive repair: audits completeness for --tickers (or the
    full scoped set) and, only with --execute-1d-migration also present,
    fetches EXACTLY the missing ranges (via migration.repair_1d, not the
    normal existing-coverage narrowing) and promotes them the same way as
    a normal run. Without --execute-1d-migration this is a dry, read-only
    audit -- zero Angel One calls, zero writes."""
    from database import SessionLocal
    from migration.config import MigrationConfig
    from migration.completeness import audit_completeness, missing_date_ranges
    from migration.staging import prepare_staging_table
    from migration.fetch_manager import AngelOneFetchManager
    from migration.validator import MigrationValidator
    from migration.repair_1d import repair_ticker_gaps
    from migration.promotion import promote_1d_candles, reconcile_1d_promotion

    ticker_filter = _parse_ticker_filter(args)
    print("[1D Repair] Planning (read-only)...")
    plan, cfg = build_plan(ticker_filter=ticker_filter)
    print(f"[1D Repair] Auditing completeness for {len(plan.identities)} ticker(s) "
          f"over [{plan.required_start}, {plan.today}]...")

    db = SessionLocal()
    try:
        targets = [(i.ticker, plan.required_start, plan.today) for i in plan.identities]
        results = audit_completeness(db, targets, timeframe="1D")
    finally:
        db.close()

    by_ticker_exchange = {i.ticker: i.exchange for i in plan.identities}
    repair_targets = {t: missing_date_ranges(r) for t, r in results.items() if not r.passed}

    print()
    print(f"[1D Repair] {len(repair_targets)}/{len(results)} ticker(s) have gaps to repair.")
    total_missing_sessions = sum(results[t].missing_sessions for t in repair_targets)
    print(f"[1D Repair] Total missing sessions: {total_missing_sessions}")
    for ticker, ranges in repair_targets.items():
        print(f"      {ticker}: {results[ticker].summary()}")
        for start, end in ranges:
            print(f"          missing range: {start} .. {end}")

    if not args.execute_1d_migration:
        print()
        print("[1D Repair] Dry run only -- no Angel One calls made, no database rows written.")
        return

    if not repair_targets:
        print("[1D Repair] Nothing to repair.")
        return

    print()
    print("[1D Repair] Loading Angel One instrument list (may download if no local cache)...")
    from angelone_service import angelone_service
    angelone_service.load_instruments()

    db = SessionLocal()
    try:
        status = prepare_staging_table(db, cfg.staging_table)
        print(f"[1D Repair] Staging table: {status}")
    finally:
        db.close()

    fetch_mgr = AngelOneFetchManager(cfg)
    validator = MigrationValidator()

    db = SessionLocal()
    try:
        for ticker, ranges in repair_targets.items():
            exchange = by_ticker_exchange[ticker]
            print(f"[1D Repair] Fetching missing ranges for {ticker} ({exchange})...")
            result = repair_ticker_gaps(db, cfg, fetch_mgr, validator, ticker, exchange, ranges)
            print(f"      chunks_attempted={result.chunks_attempted} chunks_failed={result.chunks_failed} "
                  f"candles_fetched={result.candles_fetched} candles_staged={result.candles_staged} "
                  f"validation_passed={result.validation_passed}")
            if result.errors:
                for e in result.errors[:5]:
                    print(f"          error: {e}")
    finally:
        db.close()

    db = SessionLocal()
    try:
        print("[1D Repair] Promoting repaired rows into production `candles` (additive only)...")
        promo_result = promote_1d_candles(db, staging_table=cfg.staging_table)
        print(f"[1D Repair] Promotion result: {promo_result}")

        print("[1D Repair] Reconciling staging vs. production...")
        recon = reconcile_1d_promotion(db, staging_table=cfg.staging_table)
        print(f"[1D Repair] Reconciliation: {recon}")
    finally:
        db.close()

    print("[1D Repair] Re-running completeness check on repaired tickers...")
    recheck_results = _run_completeness_check(plan, [i for i in plan.identities if i.ticker in repair_targets])
    _print_completeness_report(recheck_results)

    print("[1D Repair] Done. Staging table left in place as an audit trail.")


def main():
    parser = argparse.ArgumentParser(
        description="Scoped 1D-only historical candle migration (separate from run_migration.py's all-tier tool).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dry-run", action="store_true",
                         help="Discovery + range calculation only. Zero Angel One calls, zero writes. This is also the default when no flag is given.")
    parser.add_argument("--execute-1d-migration", action="store_true",
                         help="Required, exact flag to perform the real fetch/stage/promote run. Without it, nothing real happens.")
    parser.add_argument("--tickers", type=str, default=None,
                         help="Comma-separated ticker list to scope this run to (e.g. RELIANCE,TCS,INFY), "
                              "for a small controlled test before running the full universe. "
                              "Applies to both --dry-run and --execute-1d-migration. Omit to process everything.")
    parser.add_argument("--tickers-file", type=str, default=None,
                         help="Path to a file with one ticker per line (blank lines and #-comments ignored). "
                              "Use instead of --tickers for large lists that would exceed the command-line limit.")
    parser.add_argument("--include-inactive", action="store_true",
                         help="Allow explicitly named tickers (--tickers/--tickers-file) that are NOT "
                              "is_active=TRUE in stock_metadata. Has no effect without an explicit list, "
                              "and never widens the default universe.")
    parser.add_argument("--repair-gaps", action="store_true",
                         help="Audit completeness for --tickers (or the full scoped set) and, only with "
                              "--execute-1d-migration also present, fetch and additively promote exactly the "
                              "missing date ranges found. Without --execute-1d-migration this is a read-only "
                              "audit: zero Angel One calls, zero writes.")
    parser.add_argument("--deep-backfill-days", type=int, default=None,
                         help="Override the fetch target's lookback window (e.g. 3650 for 10yr) for THIS RUN "
                              "ONLY -- does not touch the real 730-day retention/compression policy in "
                              "migration.yaml. compute_1d_required_range() still narrows to only the OLDER gap "
                              "missing before each ticker's existing earliest row, so the already-fetched 2yr "
                              "window is never re-requested. Each ticker's gap is chunked at the 1D tier's "
                              "existing max_date_range_days (730d), safely under Angel One's ~2000-day "
                              "per-request truncation cap (a single request wider than that silently drops "
                              "the oldest portion instead of erroring -- confirmed empirically).")
    args = parser.parse_args()

    if args.repair_gaps:
        cmd_repair_gaps(args)
    elif args.execute_1d_migration:
        cmd_execute(args)
    else:
        # --dry-run explicitly, or no flag at all -- both are the safe path.
        cmd_dry_run(args)


if __name__ == "__main__":
    main()
