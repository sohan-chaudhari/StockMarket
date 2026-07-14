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

    @property
    def has_date_range(self) -> bool:
        return self.months is not None and self.months > 0

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
