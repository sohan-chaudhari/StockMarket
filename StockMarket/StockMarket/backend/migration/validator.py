import hashlib
import json
import random
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from datetime import date

from exchange_calendar import IST, nse_calendar
from aggregator import validate_ohlc


def compute_checksum(candles: List[Dict]) -> str:
    """SHA256 of canonical sorted candle data."""
    canonical = json.dumps(
        [
            (
                c["timestamp"].isoformat() if isinstance(c["timestamp"], datetime) else str(c["timestamp"]),
                float(c["open"]),
                float(c["high"]),
                float(c["low"]),
                float(c["close"]),
                int(c.get("volume", 0)),
            )
            for c in sorted(candles, key=lambda x: x["timestamp"] if isinstance(x["timestamp"], datetime) else datetime.fromisoformat(str(x["timestamp"])))
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class ValidationResult:
    def __init__(self):
        self.passed = True
        self.checks = []

    def add_check(self, name: str, passed: bool, detail: Optional[str] = None):
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.passed = False

    def summary(self) -> str:
        total = len(self.checks)
        passed_count = sum(1 for c in self.checks if c["passed"])
        return f"{passed_count}/{total} checks passed"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
        }


class MigrationValidator:
    def validate_source(self, candles: List[Dict], tier_tf: str, date_range: Optional[Tuple[date, date]]) -> ValidationResult:
        result = ValidationResult()

        if not candles:
            result.add_check("non_empty", False, "No candles received")
            return result

        result.add_check("non_empty", True, f"{len(candles)} candles")

        ohlc_ok = True
        violations = 0
        for c in candles:
            valid, msg = validate_ohlc(c["open"], c["high"], c["low"], c["close"])
            if not valid:
                violations += 1
                ohlc_ok = False
                if violations <= 3:
                    ts = c.get("timestamp", "?")
                    result.add_check(f"ohlc_violation_{violations}", False, f"{ts}: {msg}")
        result.add_check("ohlc_integrity", ohlc_ok, f"{violations} violations" if violations else "all valid")

        timestamps = [c["timestamp"] for c in candles]
        sorted_ts = sorted(timestamps)
        in_order = all(
            sorted_ts[i] <= sorted_ts[i + 1]
            for i in range(len(sorted_ts) - 1)
        )
        result.add_check("timestamp_ordering", in_order)

        unique = len(set(
            ts.isoformat() if isinstance(ts, datetime) else str(ts)
            for ts in timestamps
        ))
        no_dupes = unique == len(timestamps)
        result.add_check("no_duplicates", no_dupes, f"{len(timestamps) - unique} duplicates" if not no_dupes else None)

        if date_range:
            start_dt = datetime.combine(date_range[0], datetime.min.time()).replace(tzinfo=IST)
            end_dt = datetime.combine(date_range[1], datetime.min.time()).replace(hour=23, minute=59, tzinfo=IST)
            in_range = all(
                (isinstance(ts, datetime) and start_dt <= ts.replace(tzinfo=IST) <= end_dt) or
                (isinstance(ts, str) and str(date_range[0]) <= ts[:10] <= str(date_range[1]))
                for ts in timestamps
            )
            result.add_check("date_range", in_range)

        all_vol_positive = all(c.get("volume", 0) >= 0 for c in candles)
        result.add_check("volume_positive", all_vol_positive)

        return result

    def validate_db_readback(
        self,
        source_candles: List[Dict],
        db_candles: List[Dict],
        checksum_src: str,
        sample_size: int = 10,
    ) -> ValidationResult:
        result = ValidationResult()

        count_match = len(source_candles) == len(db_candles)
        result.add_check("count_match", count_match, f"src={len(source_candles)} db={len(db_candles)}")

        checksum_db = compute_checksum(db_candles)
        checksums_match = checksum_src == checksum_db
        result.add_check("checksum_match", checksums_match)

        if count_match and checksums_match:
            return result

        if sample_size > 0 and db_candles:
            sample = random.sample(db_candles, min(sample_size, len(db_candles)))
            src_lookup = {}
            for c in source_candles:
                ts = c["timestamp"]
                key = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                src_lookup[key] = c

            mismatches = 0
            for c in sample:
                ts = c["timestamp"]
                key = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                src = src_lookup.get(key)
                if src is None:
                    mismatches += 1
                    continue
                for field in ["open", "high", "low", "close", "volume"]:
                    sv = float(src.get(field, 0))
                    dv = float(c.get(field, 0))
                    if abs(sv - dv) > 0.001:
                        mismatches += 1
                        break
            result.add_check("spot_check", mismatches == 0, f"{mismatches}/{sample_size} mismatches")

        return result
