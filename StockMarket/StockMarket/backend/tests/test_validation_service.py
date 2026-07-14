import unittest
from datetime import datetime
from unittest.mock import MagicMock
from validation_service import ValidationService, ValidationResult


class TestValidationResult(unittest.TestCase):
    def test_create_pass(self):
        r = ValidationResult("test", "PASS", "All good", {"count": 10})
        self.assertEqual(r.check_name, "test")
        self.assertEqual(r.status, "PASS")
        self.assertEqual(r.metrics["count"], 10)


class TestValidationService(unittest.TestCase):
    def setUp(self):
        self.db_factory = MagicMock()
        self.resample_svc = MagicMock()
        self.service = ValidationService(
            db_session_factory=self.db_factory,
            resample_svc=self.resample_svc,
        )

    def test_validate_ohlc_pass(self):
        candles = [
            {"open": 100, "high": 105, "low": 95, "close": 102},
            {"open": 102, "high": 110, "low": 100, "close": 108},
        ]
        result = self.service.validate_ohlc(candles)
        self.assertEqual(result.status, "PASS")

    def test_validate_ohlc_fail(self):
        candles = [{"open": 100, "high": 90, "low": 110, "close": 102}]
        result = self.service.validate_ohlc(candles)
        self.assertEqual(result.status, "FAIL")

    def test_validate_volume_conservation_pass(self):
        source = [{"volume": 100}, {"volume": 200}]
        target = [{"volume": 300}]
        result = self.service.validate_volume_conservation(source, target)
        self.assertEqual(result.status, "PASS")

    def test_validate_volume_conservation_fail(self):
        source = [{"volume": 100}, {"volume": 200}]
        target = [{"volume": 250}]
        result = self.service.validate_volume_conservation(source, target)
        self.assertEqual(result.status, "FAIL")

    def test_validate_no_duplicates_pass(self):
        candles = [
            {"time": 1000, "open": 100},
            {"time": 1001, "open": 102},
        ]
        result = self.service.validate_no_duplicates(candles)
        self.assertEqual(result.status, "PASS")

    def test_validate_no_duplicates_fail(self):
        candles = [
            {"time": 1000, "open": 100},
            {"time": 1000, "open": 102},
        ]
        result = self.service.validate_no_duplicates(candles)
        self.assertEqual(result.status, "FAIL")

    def test_validate_no_gaps_pass(self):
        base = 1788300000
        candles = [{"time": base + i * 300} for i in range(10)]
        result = self.service.validate_no_gaps(candles, "5m")
        self.assertEqual(result.status, "PASS")

    def test_validate_no_gaps_warn(self):
        base = 1788300000
        candles = [{"time": base}, {"time": base + 100000}]  # > 2 sessions apart
        result = self.service.validate_no_gaps(candles, "5m")
        self.assertEqual(result.status, "WARN")

    def test_validate_no_gaps_skip(self):
        result = self.service.validate_no_gaps([{"time": 1000}], "5m")
        self.assertEqual(result.status, "SKIP")

    def test_validate_timestamp_monotonicity_pass(self):
        candles = [{"time": 1000}, {"time": 1001}, {"time": 1002}]
        result = self.service.validate_timestamp_monotonicity(candles)
        self.assertEqual(result.status, "PASS")

    def test_validate_timestamp_monotonicity_fail(self):
        candles = [{"time": 1002}, {"time": 1001}]
        result = self.service.validate_timestamp_monotonicity(candles)
        self.assertEqual(result.status, "FAIL")

    def test_validate_aggregation_empty(self):
        self.resample_svc.resample_5m_to.return_value = []
        result = self.service.validate_aggregation([], "15m", [])
        self.assertEqual(result.status, "PASS")

    def test_validate_aggregation_match(self):
        source = [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        target = [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        expected = list(target)
        self.resample_svc.resample_5m_to.return_value = expected
        result = self.service.validate_aggregation(source, "15m", target)
        self.assertEqual(result.status, "PASS")

    def test_validate_aggregation_mismatch(self):
        source = [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        target = [{"open": 100, "high": 106, "low": 95, "close": 102, "volume": 1000}]
        self.resample_svc.resample_5m_to.return_value = [
            {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        ]
        result = self.service.validate_aggregation(source, "15m", target)
        self.assertEqual(result.status, "FAIL")

    def test_validate_aggregation_skip_no_resampler(self):
        svc = ValidationService(db_session_factory=MagicMock())
        result = svc.validate_aggregation([], "15m", [])
        self.assertEqual(result.status, "SKIP")

    def test_run_all(self):
        candles = [{"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        results = self.service.run_all(candles, "5m")
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIsInstance(r, ValidationResult)

    def test_run_all_with_resample(self):
        source = [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        target = [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}]
        self.resample_svc.resample_5m_to.return_value = list(target)
        results = self.service.run_all(
            [{"time": 1000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}],
            "15m", source_5m=source, target_candles=target,
        )
        self.assertGreater(len(results), 4)

    def test_generate_report_all_pass(self):
        results = [ValidationResult("c1", "PASS"), ValidationResult("c2", "PASS")]
        report = self.service.generate_report(results)
        self.assertEqual(report["passed"], 2)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["overall"], "PASS")

    def test_generate_report_some_fail(self):
        results = [ValidationResult("c1", "PASS"), ValidationResult("c2", "FAIL")]
        report = self.service.generate_report(results)
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["overall"], "FAIL")

    def test_generate_report_has_details(self):
        results = [ValidationResult("test", "PASS", "All good", {"count": 10})]
        report = self.service.generate_report(results)
        self.assertEqual(len(report["details"]), 1)
        self.assertEqual(report["details"][0]["check"], "test")


if __name__ == "__main__":
    unittest.main()
