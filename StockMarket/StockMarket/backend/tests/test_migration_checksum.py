"""Regression tests for migration.validator.compute_checksum.

Root cause (found via test_batch_downloader_1d_validation.py's real failure,
not simulated): compute_checksum() is called on candle dicts from two
different origins that don't agree on the `timestamp` field's type --
freshly-fetched source candles carry a real `datetime` object, but a batch-
downloader staging-table readback (_readback_candles, raw SQL, not the ORM)
hands back whatever the DBAPI driver's default row type is for a
DATETIME/TIMESTAMP column. For SQLite (used by this project's own tests)
that's a plain string in `str(datetime)` format ("YYYY-MM-DD HH:MM:SS",
space-separated) -- NOT ISO 8601's "T" separator. The previous
implementation called `.isoformat()` on a real datetime but plain `str()`
on anything else, so the identical instant serialized two different ways
depending only on which side of a DB round-trip it came from, producing a
false-positive "checksum mismatch" with zero actual data difference.
"""
import unittest
from datetime import datetime

from migration.validator import compute_checksum, _as_datetime


def _candle(ts, close=100.0, **overrides):
    row = {"timestamp": ts, "open": 100.0, "high": 105.0, "low": 95.0, "close": close, "volume": 1000}
    row.update(overrides)
    return row


class TestAsDatetimeNormalization(unittest.TestCase):
    def test_real_datetime_passed_through(self):
        dt = datetime(2026, 8, 9, 12, 30, 0)
        self.assertEqual(_as_datetime(dt), dt)

    def test_space_separated_string_parsed(self):
        """Exactly what SQLite's raw driver readback returns for a DATETIME
        column -- str(datetime), not .isoformat()."""
        self.assertEqual(_as_datetime("2026-08-09 12:30:00"), datetime(2026, 8, 9, 12, 30, 0))

    def test_t_separated_iso_string_parsed(self):
        self.assertEqual(_as_datetime("2026-08-09T12:30:00"), datetime(2026, 8, 9, 12, 30, 0))

    def test_both_string_forms_of_the_same_instant_are_equal(self):
        self.assertEqual(_as_datetime("2026-08-09 12:30:00"), _as_datetime("2026-08-09T12:30:00"))


class TestComputeChecksumDriverIndependence(unittest.TestCase):
    """The actual bug: identical data must checksum identically regardless
    of which of the two real shapes (datetime object vs. driver-returned
    string) the timestamp arrives in."""

    def test_datetime_object_and_space_separated_string_of_same_instant_match(self):
        source_side = [_candle(datetime(2026, 8, 9, 0, 0, 0))]
        # This is EXACTLY what a raw SQLite readback of the same row looks
        # like after a round trip through _bulk_insert -> _readback_candles.
        db_side = [_candle("2026-08-09 00:00:00")]
        self.assertEqual(compute_checksum(source_side), compute_checksum(db_side))

    def test_datetime_object_and_t_separated_string_of_same_instant_match(self):
        source_side = [_candle(datetime(2026, 8, 9, 0, 0, 0))]
        db_side = [_candle("2026-08-09T00:00:00")]
        self.assertEqual(compute_checksum(source_side), compute_checksum(db_side))

    def test_multi_row_round_trip_matches(self):
        """Mirrors the real batch_downloader flow: N candles fetched with
        real datetimes, the same N candles read back as SQLite strings --
        checksums must agree exactly, proving no false-positive mismatch."""
        source_side = [
            _candle(datetime(2026, 8, 9 + i, 0, 0, 0), close=100.0 + i)
            for i in range(5)
        ]
        db_side = [
            _candle(f"2026-08-{9 + i:02d} 00:00:00", close=100.0 + i)
            for i in range(5)
        ]
        self.assertEqual(compute_checksum(source_side), compute_checksum(db_side))


class TestComputeChecksumStillDetectsRealDifferences(unittest.TestCase):
    """The fix must not weaken validation -- genuinely different data must
    still produce genuinely different checksums."""

    def test_different_close_price_changes_checksum(self):
        a = [_candle(datetime(2026, 8, 9), close=100.0)]
        b = [_candle(datetime(2026, 8, 9), close=101.0)]
        self.assertNotEqual(compute_checksum(a), compute_checksum(b))

    def test_different_timestamp_changes_checksum(self):
        a = [_candle(datetime(2026, 8, 9))]
        b = [_candle(datetime(2026, 8, 10))]
        self.assertNotEqual(compute_checksum(a), compute_checksum(b))

    def test_different_volume_changes_checksum(self):
        a = [_candle(datetime(2026, 8, 9), volume=1000)]
        b = [_candle(datetime(2026, 8, 9), volume=2000)]
        self.assertNotEqual(compute_checksum(a), compute_checksum(b))

    def test_missing_row_changes_checksum(self):
        a = [_candle(datetime(2026, 8, 9)), _candle(datetime(2026, 8, 10))]
        b = [_candle(datetime(2026, 8, 9))]
        self.assertNotEqual(compute_checksum(a), compute_checksum(b))

    def test_extra_row_changes_checksum(self):
        a = [_candle(datetime(2026, 8, 9))]
        b = [_candle(datetime(2026, 8, 9)), _candle(datetime(2026, 8, 10))]
        self.assertNotEqual(compute_checksum(a), compute_checksum(b))

    def test_row_order_does_not_affect_checksum(self):
        """Candles must be sorted before hashing -- fetch/readback order is
        not guaranteed to match, and that alone must not cause a mismatch."""
        forward = [_candle(datetime(2026, 8, 9)), _candle(datetime(2026, 8, 10), close=200.0)]
        reversed_order = [_candle(datetime(2026, 8, 10), close=200.0), _candle(datetime(2026, 8, 9))]
        self.assertEqual(compute_checksum(forward), compute_checksum(reversed_order))


if __name__ == "__main__":
    unittest.main()
