"""P2.4 — index stale-tick race regression.

`_on_angel_tick` is the single funnel both the AngelOne WebSocket and the REST
pollers (PricePoller / CriticalIndexPoller) pass through before writing
`angelone_service.latest_ticks`. It used to hardcode `_source = "angel_ws"` and
drop `_received_ts`, which disabled the pollers' own WS-freshness guard
(`existing._source == "angel_ws" and now - _received_ts < 2/5s`) -- so an older
REST snapshot could overwrite a newer WS tick.

The fix preserves the real provider and stamps `_received_ts`, restoring that
guard. These tests exercise the funnel and then evaluate the exact guard
predicate both pollers use.
"""
import time
import unittest

import main
from angelone_service import angelone_service as svc

TICKER = "ZZTESTP24"


def _inject(data):
    main._on_angel_tick(TICKER, data)
    with svc.latest_ticks_lock:
        return dict(svc.latest_ticks.get(TICKER) or {})


def _rest_guard_should_skip(existing, window=2):
    """Exact predicate CriticalIndexPoller/PricePoller use before overwriting."""
    return (existing.get("_source") == "angel_ws"
            and time.time() - existing.get("_received_ts", 0) < window)


def _ws_tick(price=100.0):
    return {"current_price": price, "current": price, "prev_close": 99.0, "volume": 1000,
            "_ts": time.time(), "_source": "angel_ws"}


def _rest_tick(price=100.0):
    return {"current_price": price, "current": price, "prev_close": 99.0, "volume": 1000,
            "_ts": time.time(), "_source": "angel_rest_poller"}


class TickProvenanceTests(unittest.TestCase):
    def tearDown(self):
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)

    def test_ws_tick_stamps_source_and_receive_time(self):
        before = time.time()
        t = _inject(_ws_tick())
        self.assertEqual(t.get("_source"), "angel_ws")
        self.assertIsInstance(t.get("_received_ts"), float)
        self.assertGreaterEqual(t["_received_ts"], before)

    def test_rest_tick_keeps_its_provenance(self):
        t = _inject(_rest_tick())
        self.assertEqual(t.get("_source"), "angel_rest_poller")

    def test_newer_ws_blocks_older_rest(self):
        _inject(_ws_tick(105.0))
        with svc.latest_ticks_lock:
            existing = dict(svc.latest_ticks[TICKER])
        self.assertTrue(_rest_guard_should_skip(existing),
                        "a fresh WS tick must block a REST overwrite (stale-tick race)")

    def test_older_rest_then_newer_ws_ws_wins(self):
        _inject(_rest_tick(100.0))
        _inject(_ws_tick(105.0))
        with svc.latest_ticks_lock:
            t = dict(svc.latest_ticks[TICKER])
        self.assertEqual(t["_source"], "angel_ws")
        self.assertAlmostEqual(t["current_price"], 105.0)

    def test_equal_timestamps_are_deterministic(self):
        ts = time.time()
        d = _ws_tick(100.0)
        d["_ts"] = ts
        _inject(d)
        with svc.latest_ticks_lock:
            self.assertTrue(_rest_guard_should_skip(dict(svc.latest_ticks[TICKER])))

    def test_rest_to_rest_polling_is_not_blocked(self):
        _inject(_rest_tick())
        with svc.latest_ticks_lock:
            existing = dict(svc.latest_ticks[TICKER])
        self.assertFalse(_rest_guard_should_skip(existing),
                         "REST->REST polling must continue (source stays rest_poller)")

    def test_reconnect_ws_tick_is_fresh_again(self):
        # simulate: old WS tick, then a reconnect resubscribe delivering a fresh one
        _inject(_ws_tick(100.0))
        with svc.latest_ticks_lock:
            svc.latest_ticks[TICKER]["_received_ts"] = time.time() - 60  # stale
        _inject(_ws_tick(106.0))  # reconnect snapshot
        with svc.latest_ticks_lock:
            t = dict(svc.latest_ticks[TICKER])
        self.assertTrue(_rest_guard_should_skip(t))
        self.assertAlmostEqual(t["current_price"], 106.0)


if __name__ == "__main__":
    unittest.main()
