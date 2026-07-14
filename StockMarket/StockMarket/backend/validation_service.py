"""
ValidationService
===================
Automated validation checks for the entire candle pipeline.

Every check returns a ValidationResult with status (PASS/FAIL) and details.
The LLM-as-a-Judge reviews the report but never makes deployment decisions.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    check_name: str
    status: str  # PASS, FAIL, WARN
    details: str = ""
    metrics: Dict = field(default_factory=dict)


class ValidationService:
    def __init__(self, db_session_factory, resample_svc=None, exchange_calendar=None):
        self._db_factory = db_session_factory
        self._resample_svc = resample_svc
        self._calendar = exchange_calendar

    def validate_ohlc(self, candles: List[Dict]) -> ValidationResult:
        errors = []
        for i, c in enumerate(candles):
            o, h, l, cv = c.get("open", 0), c.get("high", 0), c.get("low", 0), c.get("close", 0)
            if h < o or h < cv:
                errors.append({"index": i, "reason": f"high={h} < open={o} or close={cv}"})
            if l > o or l > cv:
                errors.append({"index": i, "reason": f"low={l} > open={o} or close={cv}"})
            if h < l:
                errors.append({"index": i, "reason": f"high={h} < low={l}"})
        status = "PASS" if not errors else "FAIL"
        return ValidationResult(
            check_name="ohlc_integrity",
            status=status,
            details=f"{len(candles)} candles checked, {len(errors)} errors" if errors else f"{len(candles)} candles all valid",
            metrics={"checked": len(candles), "errors": len(errors)},
        )

    def validate_volume_conservation(self, source: List[Dict], target: List[Dict]) -> ValidationResult:
        src_vol = sum(c.get("volume", 0) for c in source)
        tgt_vol = sum(c.get("volume", 0) for c in target)
        status = "PASS" if src_vol == tgt_vol else "FAIL"
        return ValidationResult(
            check_name="volume_conservation",
            status=status,
            details=f"source={src_vol}, target={tgt_vol}",
            metrics={"source_volume": src_vol, "target_volume": tgt_vol},
        )

    def validate_aggregation(self, source_5m: List[Dict], target_tf: str,
                             target_candles: List[Dict]) -> ValidationResult:
        if self._resample_svc is None:
            return ValidationResult("aggregation_accuracy", "SKIP", "No resample service")
        expected = self._resample_svc.resample_5m_to(source_5m, target_tf)
        if not expected and not target_candles:
            return ValidationResult("aggregation_accuracy", "PASS", "Both empty")
        if len(expected) != len(target_candles):
            return ValidationResult(
                "aggregation_accuracy", "FAIL",
                f"Expected {len(expected)} candles, got {len(target_candles)}",
            )
        for e, a in zip(expected, target_candles):
            for key in ["open", "high", "low", "close", "volume"]:
                if e.get(key) != a.get(key):
                    return ValidationResult(
                        "aggregation_accuracy", "FAIL",
                        f"Mismatch at {e.get('timestamp')}: {key} {e.get(key)} vs {a.get(key)}",
                    )
        return ValidationResult("aggregation_accuracy", "PASS", "All candles match")

    def validate_no_duplicates(self, candles: List[Dict]) -> ValidationResult:
        times = [c.get("time") or hash(str(c.get("timestamp"))) for c in candles]
        seen = set()
        dups = []
        for i, t in enumerate(times):
            if t in seen:
                dups.append(i)
            seen.add(t)
        status = "PASS" if not dups else "FAIL"
        return ValidationResult(
            check_name="no_duplicates",
            status=status,
            details=f"{len(dups)} duplicates found" if dups else f"{len(candles)} candles, all unique",
            metrics={"total": len(candles), "duplicates": len(dups)},
        )

    def validate_no_gaps(self, candles: List[Dict], tf: str,
                         session_minutes: int = 75) -> ValidationResult:
        if len(candles) < 2:
            return ValidationResult("no_gaps", "SKIP", "Less than 2 candles")
        times = sorted([c.get("time") or 0 for c in candles])
        gaps = []
        for i in range(1, len(times)):
            diff = times[i] - times[i - 1]
            if diff > session_minutes * 60 * 2:
                gaps.append((times[i - 1], times[i], diff))
        status = "PASS" if not gaps else "WARN"
        return ValidationResult(
            check_name="no_gaps",
            status=status,
            details=f"{len(gaps)} gaps found" if gaps else f"{len(times)} candles, no gaps",
            metrics={"total": len(times), "gaps": len(gaps)},
        )

    def validate_timestamp_monotonicity(self, candles: List[Dict]) -> ValidationResult:
        times = [c.get("time") or c.get("timestamp") for c in candles]
        for i in range(1, len(times)):
            if times[i] is not None and times[i - 1] is not None and times[i] < times[i - 1]:
                return ValidationResult(
                    "timestamp_monotonicity", "FAIL",
                    f"Candle {i} timestamp {times[i]} < previous {times[i - 1]}"
                )
        return ValidationResult("timestamp_monotonicity", "PASS", f"{len(candles)} candles monotonic")

    def run_all(self, candles: List[Dict], tf: str = "5m",
                source_5m: Optional[List[Dict]] = None,
                target_candles: Optional[List[Dict]] = None) -> List[ValidationResult]:
        results = []
        results.append(self.validate_ohlc(candles))
        results.append(self.validate_no_duplicates(candles))
        results.append(self.validate_timestamp_monotonicity(candles))
        results.append(self.validate_no_gaps(candles, tf))
        if source_5m and target_candles:
            results.append(self.validate_volume_conservation(source_5m, target_candles))
            results.append(self.validate_aggregation(source_5m, tf, target_candles))
        return results

    def generate_report(self, results: List[ValidationResult]) -> Dict:
        passed = sum(1 for r in results if r.status == "PASS")
        failed = sum(1 for r in results if r.status == "FAIL")
        warned = sum(1 for r in results if r.status == "WARN")
        return {
            "summary": f"{passed} passed, {failed} failed, {warned} warnings",
            "passed": passed,
            "failed": failed,
            "warnings": warned,
            "details": [
                {"check": r.check_name, "status": r.status, "details": r.details, "metrics": r.metrics}
                for r in results
            ],
            "overall": "PASS" if failed == 0 else "FAIL",
        }
