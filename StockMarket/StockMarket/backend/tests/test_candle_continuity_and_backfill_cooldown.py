"""Phase 2/4 regression tests: 5m candle continuity + backfill cooldown.

Covers:
  * PendingCandleManager.get / Live5mBuilder.get_pending -- visibility of a
    completed candle while it is still inside the late-tick buffer (before it is
    written to the DB).
  * main._merge_live_5m -- dedup during/after the flush transition, persisted-row
    precedence, ordering, never appending the same candle twice.
  * Backfill cooldown -- dispatch suppression, retryable vs permanent provider
    failures, recovery after expiry, bounded/concurrent-safe state.
"""
import threading
import time
import unittest
from datetime import datetime, timedelta

import main
from aggregator import PendingCandleManager, Live5mBuilder


def _candle(ts, o, h, l, c, v):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


class PendingCandleManagerTests(unittest.TestCase):
    def setUp(self):
        self.mgr = PendingCandleManager(late_buffer_sec=60)

    def test_get_none_when_empty(self):
        self.assertIsNone(self.mgr.get("NOPE"))

    def test_get_returns_pending_before_persistence(self):
        now = time.time()
        self.mgr.add_or_update("X", _candle(datetime(2026, 10, 9, 14, 20), 1, 2, 0.5, 1.5, 10), now)
        p = self.mgr.get("X")
        self.assertIsNotNone(p)
        self.assertEqual((p.open, p.high, p.low, p.close, p.volume), (1, 2, 0.5, 1.5, 10))

    def test_get_none_after_expiry(self):
        now = time.time()
        self.mgr.add_or_update("X", _candle(datetime(2026, 10, 9, 14, 20), 1, 2, 0.5, 1.5, 10), now)
        expired = self.mgr.get_expired(now + 61)
        self.assertEqual(len(expired), 1)
        self.assertIsNone(self.mgr.get("X"))

    def test_not_expired_before_deadline(self):
        now = time.time()
        self.mgr.add_or_update("X", _candle(datetime(2026, 10, 9, 14, 20), 1, 2, 0.5, 1.5, 10), now)
        self.assertEqual(self.mgr.get_expired(now + 30), [])
        self.assertIsNotNone(self.mgr.get("X"))

    def test_concurrent_reads_while_flush_removes(self):
        now = time.time()
        self.mgr.add_or_update("X", _candle(datetime(2026, 10, 9, 14, 20), 1, 2, 0.5, 1.5, 10), now)
        errors = []
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    self.mgr.get("X")
            except Exception as e:  # pragma: no cover - only on failure
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.05)
        self.mgr.get_expired(now + 61)  # flush removes it mid-read
        time.sleep(0.05)
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class GetPendingShapeTests(unittest.TestCase):
    def test_get_pending_none_when_empty(self):
        self.assertIsNone(Live5mBuilder().get_pending("ZZNONE"))

    def test_get_pending_shape_and_ohlcv(self):
        b = Live5mBuilder()
        b._pending_mgr.add_or_update(
            "ZZPEND", _candle(datetime(2026, 10, 9, 14, 20), 100, 105, 99, 104, 500), time.time())
        got = b.get_pending("ZZPEND")
        self.assertIn("5m", got)
        c = got["5m"]
        self.assertEqual((c["open"], c["high"], c["low"], c["close"], c["volume"]), (100, 105, 99, 104, 500))
        self.assertIsInstance(c["time"], int)

    def test_get_pending_repairs_invalid_ohlc(self):
        b = Live5mBuilder()
        # high (99) below close (104) is invalid -- fix_ohlc must repair it.
        b._pending_mgr.add_or_update(
            "ZZBAD", _candle(datetime(2026, 10, 9, 14, 20), 100, 99, 99, 104, 1), time.time())
        c = b.get_pending("ZZBAD")["5m"]
        self.assertGreaterEqual(c["high"], c["close"])
        self.assertLessEqual(c["low"], c["close"])


class MergeLive5mTests(unittest.TestCase):
    def test_pending_appended_when_not_persisted(self):
        data = [{"time": 100, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        main._merge_live_5m(data, {"time": 200, "open": 2, "high": 3, "low": 1, "close": 2, "volume": 5}, None)
        self.assertEqual([c["time"] for c in data], [100, 200])

    def test_pending_skipped_when_persisted(self):
        data = [{"time": 200, "open": 9, "high": 9, "low": 9, "close": 9, "volume": 9}]
        main._merge_live_5m(data, {"time": 200, "open": 2, "high": 3, "low": 1, "close": 2, "volume": 5}, None)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["close"], 9)  # persisted row wins

    def test_no_double_append_during_flush_transition(self):
        data = []
        pending = {"time": 200, "open": 2, "high": 3, "low": 1, "close": 2, "volume": 5}
        main._merge_live_5m(data, pending, None)
        main._merge_live_5m(data, pending, None)  # two polls before the row lands
        self.assertEqual(len(data), 1)

    def test_forming_replaces_same_bucket(self):
        data = [{"time": 300, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        main._merge_live_5m(data, None, {"time": 300, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 7})
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["close"], 2)

    def test_forming_appended_when_newer(self):
        data = [{"time": 300, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        main._merge_live_5m(data, None, {"time": 600, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2})
        self.assertEqual([c["time"] for c in data], [300, 600])

    def test_forming_not_appended_when_older(self):
        data = [{"time": 600, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        main._merge_live_5m(data, None, {"time": 300, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2})
        self.assertEqual([c["time"] for c in data], [600])

    def test_ordering_pending_then_forming(self):
        data = [{"time": 100}]
        main._merge_live_5m(data, {"time": 200}, {"time": 300})
        self.assertEqual([c["time"] for c in data], [100, 200, 300])

    def test_none_inputs_are_noop(self):
        data = [{"time": 100}]
        main._merge_live_5m(data, None, None)
        self.assertEqual([c["time"] for c in data], [100])

    def test_empty_data_with_forming_only(self):
        data = []
        main._merge_live_5m(data, None, {"time": 300})
        self.assertEqual([c["time"] for c in data], [300])


class InitTickerOpenTests(unittest.TestCase):
    """Phase 3b: the session's FIRST bucket opens at the official day open even
    when the ticker is subscribed mid-bucket; a later bucket is seeded at the
    first observed price (an approximation, never the true first trade)."""

    @staticmethod
    def _session_open():
        from aggregator import nse_session_open_epoch
        base = datetime(2026, 10, 9, 12, 0, tzinfo=main.IST).timestamp()
        return nse_session_open_epoch(base, None)

    def test_nse_session_open_epoch_is_0915_ist(self):
        so = self._session_open()
        ist = datetime.fromtimestamp(so, main.IST)
        self.assertEqual((ist.hour, ist.minute), (9, 15))

    def test_first_bucket_seeds_open_at_day_open(self):
        b = Live5mBuilder()
        b._init_ticker(self._session_open() + 60, "ZZFIRST", price=105.0, day_open=100.0)
        self.assertEqual(b.active_candles["ZZFIRST"]["5m"]["open"], 100.0)

    def test_first_bucket_without_day_open_uses_price(self):
        b = Live5mBuilder()
        b._init_ticker(self._session_open() + 60, "ZZFIRST2", price=105.0, day_open=0)
        self.assertEqual(b.active_candles["ZZFIRST2"]["5m"]["open"], 105.0)

    def test_later_bucket_seeds_open_at_first_observed_price(self):
        b = Live5mBuilder()
        b._init_ticker(self._session_open() + 3600, "ZZLATER", price=105.0, day_open=100.0)
        self.assertEqual(b.active_candles["ZZLATER"]["5m"]["open"], 105.0)


class BackfillCooldownStateTests(unittest.TestCase):
    def setUp(self):
        main._backfill_cooldown.clear()

    def tearDown(self):
        main._backfill_cooldown.clear()

    def test_not_active_initially(self):
        self.assertFalse(main._backfill_cooldown_active("X:5m"))

    def test_active_after_set(self):
        main._set_backfill_cooldown("X:5m", 60)
        self.assertTrue(main._backfill_cooldown_active("X:5m"))

    def test_expiry_clears_key(self):
        main._set_backfill_cooldown("X:5m", -1)  # already past
        self.assertFalse(main._backfill_cooldown_active("X:5m"))
        self.assertNotIn("X:5m", main._backfill_cooldown)

    def test_bounded_size(self):
        n = main._BACKFILL_COOLDOWN_MAX_KEYS
        for i in range(n + 50):
            main._set_backfill_cooldown(f"T{i}:5m", 60)
        self.assertLessEqual(len(main._backfill_cooldown), n)

    def test_durations_ordered(self):
        self.assertLessEqual(main._BACKFILL_OK_COOLDOWN_SEC, 120)  # short enough for new gaps
        self.assertGreater(main._BACKFILL_FAIL_COOLDOWN_SEC, main._BACKFILL_OK_COOLDOWN_SEC)
        self.assertGreater(main._BACKFILL_PERMANENT_COOLDOWN_SEC, main._BACKFILL_FAIL_COOLDOWN_SEC)

    def test_concurrent_set_and_read(self):
        errors = []

        def worker(prefix):
            try:
                for i in range(200):
                    main._set_backfill_cooldown(f"{prefix}{i}:5m", 60)
                    main._backfill_cooldown_active(f"{prefix}{i}:5m")
            except Exception as e:  # pragma: no cover - only on failure
                errors.append(e)

        ts = [threading.Thread(target=worker, args=(f"W{k}",)) for k in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])


class FakeSession:
    """Minimal stand-in for a SQLAlchemy Session (5m permanent/retryable errors
    never reach the DB query path)."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def rollback(self):
        pass

    def commit(self):
        pass

    def query(self, *a, **k):
        raise AssertionError("DB query must not run for a 5m backfill with no candles")

    def execute(self, *a, **k):
        pass


class PerformBackfillCooldownTests(unittest.TestCase):
    def setUp(self):
        main._backfill_cooldown.clear()
        main.backfill_locks.clear()
        self._orig_hist = main.historical_service
        self._orig_session = main.database.SessionLocal
        self._orig_yf = main._fetch_yfinance_intraday
        self._orig_open = main.is_market_open_now
        main.database.SessionLocal = lambda: FakeSession()
        main.is_market_open_now = lambda *a, **k: True  # skip closed-market guard

    def tearDown(self):
        main.historical_service = self._orig_hist
        main.database.SessionLocal = self._orig_session
        main._fetch_yfinance_intraday = self._orig_yf
        main.is_market_open_now = self._orig_open
        main._backfill_cooldown.clear()
        main.backfill_locks.clear()

    def _now(self):
        return main.database.get_ist_now()

    def test_permanent_error_skips_yfinance_and_parks_longest(self):
        class Hist:
            is_logged_in = True

            def login(self):
                return True

            def get_historical_candles(self, **kw):
                raise main.HistoricalFetchError("Ticker 'ZZX' not found", retryable=False)

        main.historical_service = Hist()
        yf_calls = []
        main._fetch_yfinance_intraday = lambda *a, **k: (yf_calls.append(1), [])[1]
        now = self._now()
        main.perform_on_demand_backfill("ZZX", "5m", now - timedelta(days=1), now)
        self.assertEqual(yf_calls, [], "permanent error must not trigger a yfinance call")
        self.assertTrue(main._backfill_cooldown_active("ZZX:5m"))

    def test_retryable_error_tries_yfinance(self):
        class Hist:
            is_logged_in = True

            def login(self):
                return True

            def get_historical_candles(self, **kw):
                raise main.HistoricalFetchError("exceeding access rate", retryable=True)

        main.historical_service = Hist()
        yf_calls = []
        main._fetch_yfinance_intraday = lambda *a, **k: (yf_calls.append(1), [])[1]
        now = self._now()
        main.perform_on_demand_backfill("ZZY", "5m", now - timedelta(days=1), now)
        self.assertEqual(len(yf_calls), 1, "retryable error must fall through to yfinance")
        self.assertTrue(main._backfill_cooldown_active("ZZY:5m"))

    def test_repeat_dispatch_during_cooldown_makes_no_provider_call(self):
        class Hist:
            is_logged_in = True
            calls = 0

            def login(self):
                return True

            def get_historical_candles(self, **kw):
                Hist.calls += 1
                raise main.HistoricalFetchError("exceeding access rate", retryable=True)

        main.historical_service = Hist()
        main._fetch_yfinance_intraday = lambda *a, **k: []
        now = self._now()
        main.perform_on_demand_backfill("ZZZ", "5m", now - timedelta(days=1), now)
        first = Hist.calls
        main.perform_on_demand_backfill("ZZZ", "5m", now - timedelta(days=1), now)  # within cooldown
        self.assertEqual(Hist.calls, first, "cooldown must suppress the repeat dispatch")

    def test_recovery_after_cooldown_expiry(self):
        class Hist:
            is_logged_in = True
            calls = 0

            def login(self):
                return True

            def get_historical_candles(self, **kw):
                Hist.calls += 1
                raise main.HistoricalFetchError("exceeding access rate", retryable=True)

        main.historical_service = Hist()
        main._fetch_yfinance_intraday = lambda *a, **k: []
        now = self._now()
        main.perform_on_demand_backfill("ZZW", "5m", now - timedelta(days=1), now)
        first = Hist.calls
        main._set_backfill_cooldown("ZZW:5m", -1)  # force expiry
        main.perform_on_demand_backfill("ZZW", "5m", now - timedelta(days=1), now)
        self.assertEqual(Hist.calls, first + 1, "a dispatch after cooldown expiry must retry")


if __name__ == "__main__":
    unittest.main()
