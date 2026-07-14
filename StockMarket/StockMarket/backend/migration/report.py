from datetime import datetime
from typing import List, Dict, Optional


def generate_report(
    start_time: datetime,
    end_time: datetime,
    tier_summaries: List[dict],
    failed_jobs: List[dict],
    stats: dict,
    total_tickers: int,
    elapsed_formatted: str,
    db_size_bytes: Optional[int] = None,
) -> str:
    lines = []
    sep = "=" * 55

    lines.append(sep)
    lines.append("  MIGRATION REPORT")
    lines.append(sep)
    lines.append("")

    lines.append(f"  Started:       {start_time.strftime('%Y-%m-%d %H:%M:%S')} IST")
    lines.append(f"  Completed:     {end_time.strftime('%Y-%m-%d %H:%M:%S')} IST")
    lines.append(f"  Elapsed:       {elapsed_formatted}")
    lines.append("")

    lines.append(f"  TICKERS")
    done = sum(t["done"] for t in tier_summaries) if tier_summaries else 0
    failed = sum(t["failed"] for t in tier_summaries) if tier_summaries else 0
    lines.append(f"    Total:       {total_tickers}")
    lines.append(f"    Done:        {done // max(len(tier_summaries), 1)}")
    lines.append(f"    Failed:      {len(failed_jobs)}")

    total_candles = 0
    lines.append("")
    lines.append(f"  CANDLES BY TIER")
    for t in tier_summaries:
        tf = t.get("timeframe", "?")
        c = t.get("total_rows", 0)
        total_candles += c
        lines.append(f"    Tier {t['tier']} ({tf:>4s}):  {c:>12,}")
    lines.append(f"    {'-' * 25}")
    lines.append(f"    Total:       {total_candles:>12,}")
    lines.append("")

    total_retries = sum(t.get("total_retries", 0) for t in tier_summaries)
    lines.append(f"  ERRORS:          {len(failed_jobs)}")
    lines.append(f"  RETRIES:         {total_retries}")
    lines.append("")

    if failed_jobs:
        lines.append(f"  FAILED TICKERS:")
        for j in failed_jobs[:20]:
            err_short = (j.get("error") or "?")[:80]
            lines.append(f"    {j['ticker']:<20s} Tier {j['tier']} ({j['timeframe']:>4s}): {err_short}")
        if len(failed_jobs) > 20:
            lines.append(f"    ... and {len(failed_jobs) - 20} more")
        lines.append("")

    if db_size_bytes is not None:
        lines.append(f"  DB SIZE:         {format_bytes(db_size_bytes)}")
        lines.append("")

    lines.append(f"  VALIDATION")
    checksum_fails = stats.get("total_checksum_failures", 0)
    lines.append(f"    Checksum matches:  {done - checksum_fails} / {done}")
    lines.append(f"    Checksum failures: {checksum_fails}")

    total_api = stats.get("total_api_calls", 0)
    total_failed = stats.get("failed_api_calls", 0)
    lines.append(f"    API calls:         {total_api}")
    lines.append(f"    API failures:      {total_failed}")

    lines.append("")
    lines.append(sep)

    return "\n".join(lines)


def format_bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
