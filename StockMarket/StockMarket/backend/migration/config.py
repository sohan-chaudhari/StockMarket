import os
import yaml
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from exchange_calendar import IST, nse_calendar


@dataclass
class TierConfig:
    timeframe: str
    months: Optional[int]
    angel_interval: str
    fetch_method: str
    resample_from: Optional[str] = None
    max_date_range_days: Optional[int] = None

    @property
    def has_date_range(self) -> bool:
        return self.months is not None and self.months > 0

    def effective_max_date_range_days(self, global_default: int) -> int:
        """Per-tier chunk width, falling back to the global default.

        Angel One's maximum span per request depends on the interval, but
        migration.yaml only had ONE global `angel_one.max_date_range_days`
        (90) applied to every tier. 90 is correct for the intraday intervals
        and must stay -- but it forced the 1D tier to split its 730-day
        retention window into 8 separate requests when Angel One serves the
        whole window in one.

        Only set a tier override to a span that has actually been VERIFIED
        against the live API for that interval -- never to an assumed or
        undocumented limit."""
        if self.max_date_range_days is not None and self.max_date_range_days > 0:
            return self.max_date_range_days
        return global_default

    def __post_init__(self):
        if self.fetch_method == "resample" and not self.resample_from:
            raise ValueError(f"Tier {self.timeframe}: resample requires resample_from")


@dataclass
class MigrationConfig:
    requests_per_second: int = 2
    workers: int = 3
    batch_size: int = 50
    retry_max: int = 3
    backoff_seconds: List[int] = field(default_factory=lambda: [2, 4, 8])
    use_historical_credentials: bool = True
    max_date_range_days: int = 90
    staging_table: str = "candles_migration"
    backup_retention_days: int = 7
    tiers: List[TierConfig] = field(default_factory=list)
    validate_sample_size: int = 10

    @classmethod
    def load(cls, path: Optional[str] = None) -> "MigrationConfig":
        if path is None:
            path = os.path.join(os.path.dirname(__file__), "migration.yaml")
        with open(path, "r") as f:
            raw = yaml.safe_load(f)

        cfg = cls(
            requests_per_second=raw.get("requests_per_second", 2),
            workers=raw.get("workers", 3),
            batch_size=raw.get("batch_size", 50),
            retry_max=raw.get("retry_max", 3),
            backoff_seconds=raw.get("backoff_seconds", [2, 4, 8]),
            use_historical_credentials=raw.get("angel_one", {}).get("use_historical_credentials", True),
            max_date_range_days=raw.get("angel_one", {}).get("max_date_range_days", 90),
            staging_table=raw.get("staging_table", "candles_migration"),
            backup_retention_days=raw.get("backup_retention_days", 7),
            validate_sample_size=raw.get("validation", {}).get("sample_size", 10),
        )

        for t in raw.get("tiers", []):
            cfg.tiers.append(TierConfig(
                timeframe=t["timeframe"],
                months=t.get("months"),
                angel_interval=t["angel_interval"],
                fetch_method=t["fetch_method"],
                resample_from=t.get("resample_from"),
                max_date_range_days=t.get("max_date_range_days"),
            ))

        return cfg


def get_last_trading_day() -> date:
    latest = datetime.now(IST).date()
    while not nse_calendar.is_trading_day(latest):
        latest -= timedelta(days=1)
    return latest


def compute_tier_date_ranges(cfg: MigrationConfig) -> Dict[str, Tuple[date, date]]:
    anchor = get_last_trading_day()
    ranges: Dict[str, Tuple[date, date]] = {}

    EARLIEST = date(2000, 1, 1)
    cumulative_months = 0

    for tier in cfg.tiers:
        if tier.has_date_range:
            end_offset = cumulative_months
            start_offset = cumulative_months + tier.months
            end_date = anchor - _month_offset(end_offset)
            start_date = anchor - _month_offset(start_offset)
            ranges[tier.timeframe] = (start_date, end_date)
            cumulative_months += tier.months
        else:
            end_date = anchor - _month_offset(cumulative_months) - timedelta(days=1)
            ranges[tier.timeframe] = (EARLIEST, end_date)

    return ranges


def _month_offset(months: int) -> timedelta:
    return timedelta(days=months * 30)


def chunk_date_range(start: date, end: date, max_days: int) -> List[Tuple[date, date]]:
    """Split [start, end] into contiguous, non-overlapping chunks no wider than
    max_days. Single shared implementation -- BatchDownloader._chunk_date_range
    delegates here instead of duplicating this logic (there must be exactly
    one chunking algorithm, not two that could silently drift apart).

    BUG FIXED HERE (found by test_migration_1d_range.py, pre-existing in the
    original BatchDownloader._chunk_date_range this was extracted from): the
    loop previously used `while current < end`, which silently dropped the
    final day of the range whenever (end-start).days fell strictly between
    max_days+1 and 2*max_days -- e.g. a 91-day range at max_days=90 produced
    ONE 90-day chunk and quietly lost the last day, because after that chunk
    `current` advanced to exactly `end` and the loop condition `current < end`
    was already false. `while current <= end` covers the true end date."""
    if (end - start).days <= max_days:
        return [(start, end)]
    chunks = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=max_days), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def get_1d_retention_days() -> int:
    """Single source of truth for the 1D->1W calendar-day retention window:
    reads the LIVE, approved policy (config/retention_policy.py) instead of
    hardcoding 730 a second time here, so the scoped 1D migration entrypoint
    (run_1d_migration.py) can never silently drift from the approved policy
    if it's ever changed. Per the explicit instruction: this is calendar
    days, never converted to trading sessions."""
    from config.retention_policy import RETENTION_POLICY
    for rule in RETENTION_POLICY["retention_policy"]:
        if rule.get("source_tf") == "1D" and rule.get("target_tf") == "1W":
            days = rule.get("after_days")
            if days:
                return days
    raise RuntimeError("No 1D->1W after_days rule found in the active retention policy "
                        "(config/retention_policy.py) -- refusing to guess 730.")


def compute_1d_required_range(
    required_start: date,
    today: date,
    existing_start: Optional[date] = None,
) -> Optional[Tuple[date, date]]:
    """
    Per-ticker gap-aware range for the 1D tier specifically.

    Unlike compute_tier_date_ranges() (a single global range applied
    uniformly to every ticker in a tier -- correct for the other tiers,
    which is why this is a NEW function rather than a change to that one),
    the 1D migration must fetch only the calendar-day gap between what the
    730-day retention policy requires and what a given ticker already has in
    `candles`, per item B of the migration-preparation spec ("fetch only the
    missing required history rather than blindly duplicating existing rows").

    Args:
        required_start: today - 730 calendar days (the 1D retention policy's
            required coverage start -- caller computes this so the 730-day
            constant lives in exactly one place, config/retention_policy.py,
            not duplicated here).
        today: anchor date (server "now", not necessarily a trading day --
            weekends/holidays are NOT special-cased, matching the calendar-day
            semantics the 1D->1W retention rule itself already uses).
        existing_start: the ticker's earliest existing 1D candle in `candles`,
            or None if it has no 1D data at all.

    Returns:
        (fetch_from, fetch_to) covering exactly the missing range, or None if
        the ticker already satisfies the required coverage (existing_start is
        on or before required_start) -- nothing to fetch, not even an empty
        no-op request.

    Note on "ticker with less than 730 days of actual listing history": this
    function does not and cannot know a ticker's listing date -- it only
    ever requests up to `required_start`. If the security listed more
    recently than that, Angel One's response for the pre-listing portion of
    the range is simply empty; that is the correct behavior and is validated
    downstream by MigrationValidator, not pre-guessed here.
    """
    if existing_start is not None and existing_start <= required_start:
        return None  # already satisfies the 730-day requirement, nothing to fetch
    fetch_to = existing_start - timedelta(days=1) if existing_start is not None else today
    if fetch_to < required_start:
        return None
    return (required_start, fetch_to)


def estimate_candles_per_ticker(tier: TierConfig, trading_days: int = 44) -> int:
    estimates = {
        "5m": trading_days * 75,
        "15m": trading_days * 25,
        "30m": trading_days * 13,
        "1h": trading_days * 6,
        "4h": trading_days * 2,
        "1d": trading_days,
        "1w": trading_days // 5,
        "1m": 12,
    }
    return estimates.get(tier.timeframe, 100)
