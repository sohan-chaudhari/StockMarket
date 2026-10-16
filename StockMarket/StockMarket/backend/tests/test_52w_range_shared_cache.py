"""The 52-week high/low scan is ~19 s and 280,033 disk blocks -- it seq-scans
essentially the whole candles heap, because a year of 1D rows for ~5,500 tickers
is spread across it. It was cached in-process for 6h, but gunicorn runs several
workers with private caches, so it ran once per worker per 6h (~14x/day measured)
and every run evicted the 128 MB buffer cache.

These tests pin the shared-file cache that makes it ~4x/day instead, and every
failure mode that must degrade to "just recompute" rather than raising into a
request path. Freshness (6h TTL) and the returned shape are asserted unchanged.
"""
import json
import os
import tempfile
import time
import types
import unittest

import main


class _TempPath(unittest.TestCase):
    """Redirect the shared cache file into a scratch directory."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_path = main._52w_cache_path
        main._52w_cache_path = lambda: os.path.join(self._tmp, "ranges.json")

    def tearDown(self):
        main._52w_cache_path = self._orig_path


class TestReadCacheFile(_TempPath):
    def test_missing_file_is_not_an_error(self):
        self.assertEqual(main._read_52w_cache_file(), (None, 0.0))

    def test_corrupt_file_is_not_an_error(self):
        with open(main._52w_cache_path(), "w") as fh:
            fh.write("{not json at all")
        self.assertEqual(main._read_52w_cache_file(), (None, 0.0))

    def test_truncated_file_is_not_an_error(self):
        # exactly what a torn write would look like
        with open(main._52w_cache_path(), "w") as fh:
            fh.write('{"ts": 1.0, "ranges": {"X": [1')
        self.assertEqual(main._read_52w_cache_file(), (None, 0.0))

    def test_empty_ranges_is_treated_as_no_cache(self):
        with open(main._52w_cache_path(), "w") as fh:
            json.dump({"ts": time.time(), "ranges": {}}, fh)
        self.assertEqual(main._read_52w_cache_file(), (None, 0.0))

    def test_valid_file_round_trips(self):
        ts = time.time()
        main._write_52w_cache_file({"A": (10.5, 4.25), "B": (99.0, 1.0)}, ts)
        ranges, got_ts = main._read_52w_cache_file()
        self.assertEqual(ranges, {"A": (10.5, 4.25), "B": (99.0, 1.0)})
        self.assertAlmostEqual(got_ts, ts, places=3)

    def test_bad_entries_are_skipped_not_fatal(self):
        with open(main._52w_cache_path(), "w") as fh:
            json.dump({"ts": time.time(), "ranges": {
                "GOOD": [10.0, 5.0],
                "SHORT": [1.0],
                "ZERO": [0.0, 5.0],
                "NEG": [-1.0, 5.0],
                "TEXT": ["x", "y"],
            }}, fh)
        ranges, _ = main._read_52w_cache_file()
        self.assertEqual(ranges, {"GOOD": (10.0, 5.0)})

    def test_write_is_atomic_no_tmp_left_behind(self):
        main._write_52w_cache_file({"A": (1.0, 0.5)}, time.time())
        self.assertTrue(os.path.exists(main._52w_cache_path()))
        self.assertFalse(os.path.exists(main._52w_cache_path() + ".tmp"))

    def test_unwritable_location_is_not_fatal(self):
        main._52w_cache_path = lambda: "/proc/nonexistent-dir/ranges.json"
        main._write_52w_cache_file({"A": (1.0, 0.5)}, time.time())   # must not raise


class TestLoadAdoptsSiblingCache(_TempPath):
    """_load_52w_ranges must not repeat a scan another worker already paid for."""

    def setUp(self):
        super().setUp()
        self._orig_data = main._52w_cache["data"]
        self._orig_ts = main._52w_cache["ts"]
        self._orig_loader = main._async_load_52w_ranges
        self._orig_threading = main.threading
        self.started = []

        # A thread stub that runs its target synchronously, so assertions are
        # deterministic and no real DB work happens in a unit test.
        def _loader():
            self.started.append(1)

        main._async_load_52w_ranges = _loader

        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kwargs):
                self._target = target

            def start(self):
                if self._target:
                    self._target()

        main.threading = types.SimpleNamespace(Thread=_SyncThread,
                                               Lock=main.threading.Lock)
        main._52w_cache["data"] = None
        main._52w_cache["ts"] = 0.0

    def tearDown(self):
        main._async_load_52w_ranges = self._orig_loader
        main.threading = self._orig_threading
        main._52w_cache["data"] = self._orig_data
        main._52w_cache["ts"] = self._orig_ts
        super().tearDown()

    def test_fresh_sibling_file_is_adopted_without_rescanning(self):
        main._write_52w_cache_file({"A": (10.0, 5.0)}, time.time())
        got = main._load_52w_ranges()
        self.assertEqual(got, {"A": (10.0, 5.0)})
        self.assertEqual(self.started, [],
                         "a fresh sibling cache must prevent a repeat scan")

    def test_stale_sibling_file_triggers_a_refresh(self):
        main._write_52w_cache_file({"A": (10.0, 5.0)},
                                   time.time() - main._52W_CACHE_TTL - 60)
        main._load_52w_ranges()
        self.assertEqual(len(self.started), 1,
                         "a stale file must trigger exactly one refresh")

    def test_no_file_triggers_a_refresh(self):
        main._load_52w_ranges()
        self.assertEqual(len(self.started), 1)

    def test_own_fresh_cache_wins_and_does_not_rescan(self):
        main._52w_cache["data"] = {"MINE": (7.0, 3.0)}
        main._52w_cache["ts"] = time.time()
        main._write_52w_cache_file({"THEIRS": (10.0, 5.0)}, time.time())
        self.assertEqual(main._load_52w_ranges(), {"MINE": (7.0, 3.0)})
        self.assertEqual(self.started, [])

    def test_stale_own_cache_returns_something_rather_than_empty(self):
        # Behaviour preserved from before: a stale value is served while the
        # refresh runs in the background, so callers never suddenly get {}.
        main._52w_cache["data"] = {"OLD": (7.0, 3.0)}
        main._52w_cache["ts"] = time.time() - main._52W_CACHE_TTL - 60
        self.assertEqual(main._load_52w_ranges(), {"OLD": (7.0, 3.0)})
        self.assertEqual(len(self.started), 1)

    def test_freshness_ttl_is_unchanged(self):
        self.assertEqual(main._52W_CACHE_TTL, 21600,
                         "the 6h TTL must not change: that would alter staleness")

    def test_returned_shape_matches_the_original(self):
        # {ticker: (high, low)} with floats -- what the internals widget expects.
        main._write_52w_cache_file({"A": (10.0, 5.0)}, time.time())
        got = main._load_52w_ranges()
        self.assertIsInstance(got, dict)
        for tkr, pair in got.items():
            self.assertIsInstance(tkr, str)
            self.assertIsInstance(pair, tuple)
            self.assertEqual(len(pair), 2)
            self.assertIsInstance(pair[0], float)
            self.assertIsInstance(pair[1], float)


if __name__ == "__main__":
    unittest.main()
