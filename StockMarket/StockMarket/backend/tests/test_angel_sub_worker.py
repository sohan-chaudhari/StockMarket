"""Focused behavioural tests for the Angel One subscription worker."""

import pathlib
import queue
import threading
import time

from angel_sub_worker import AngelSubWorker


def _wait(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_concurrency_serialised_and_all_complete():
    w = AngelSubWorker(logger=lambda m: None)
    lock = threading.Lock()
    st = {"run": 0, "peak": 0}
    hits = []
    done = threading.Event()

    def op(i):
        with lock:
            st["run"] += 1
            st["peak"] = max(st["peak"], st["run"])
        time.sleep(0.003)
        with lock:
            st["run"] -= 1
        hits.append(i)
        if len(hits) == 50:
            done.set()

    before = threading.active_count()
    try:
        for i in range(50):
            assert w.submit(op, i) is True
        assert done.wait(6), "only %d of 50 ran" % len(hits)
        assert st["peak"] == 1, st["peak"]
        assert threading.active_count() <= before + 1
    finally:
        assert w.stop() is True


def test_bounded_queue_rejects_observably():
    w = AngelSubWorker(max_pending=4, logger=lambda m: None)
    logs = []
    w._log = logs.append
    gate = threading.Event()
    assert w.submit(gate.wait, 5) is True
    accepted = sum(1 for _ in range(20) if w.submit(lambda: None))
    gate.set()
    try:
        assert accepted <= 4, accepted
        assert w.stats()["dropped"] >= 16, w.stats()
        assert w.stats()["max_pending_seen"] <= 4
        assert any("queue full" in m for m in logs)
    finally:
        assert w.stop() is True


def test_exception_is_counted_and_consumer_survives():
    w = AngelSubWorker(logger=lambda m: None)

    def boom():
        raise RuntimeError("kaboom")

    seen = []
    try:
        assert w.submit(boom) is True
        assert w.submit(seen.append, "after") is True
        assert _wait(lambda: "after" in seen)
        s = w.stats()
        assert s["failed"] == 1, s
        assert s["done"] >= 1, s
    finally:
        assert w.stop() is True


def test_arguments_order_and_duplicates():
    w = AngelSubWorker(logger=lambda m: None)
    seen = []
    try:
        assert w.submit(seen.append, "SUB:A") is True
        assert w.submit(seen.append, "UNSUB:B") is True
        assert w.submit(seen.append, "SUB:A") is True
        assert _wait(lambda: len(seen) == 3)
        assert seen == ["SUB:A", "UNSUB:B", "SUB:A"], seen
    finally:
        assert w.stop() is True


def test_lifecycle_idempotent_start_and_clean_stop():
    w = AngelSubWorker(logger=lambda m: None)
    t1 = w.start()
    t2 = w.start()
    assert t1 is t2
    assert w.stats()["started"] == 1
    assert w.stop() is True
    assert not t1.is_alive()
    assert [t for t in threading.enumerate() if t.name.startswith("angel-sub")] == []


def test_isolation_between_instances():
    a = AngelSubWorker(logger=lambda m: None)
    b = AngelSubWorker(logger=lambda m: None)
    seen_a, seen_b = [], []
    try:
        assert a.submit(seen_a.append, "only-a") is True
        assert _wait(lambda: seen_a == ["only-a"])
        assert seen_b == []
        assert b.stats()["started"] == 0
        assert a.stats()["started"] == 1
        assert b.pending() == 0
    finally:
        a.stop(); b.stop()


def test_rejected_subscribe_is_reconciled():
    w = AngelSubWorker(max_pending=1, logger=lambda m: None, reconcile_interval=0.05)
    calls = []
    def subscribe_tickers(tickers):
        calls.append(("sub", sorted(tickers)))
    gate = threading.Event()
    assert w.submit(gate.wait, 3) is True          # occupies the consumer
    assert w.submit(subscribe_tickers, ["AAA", "BBB"]) is False
    assert w.stats()["pending_intents"] == 2, w.stats()
    gate.set()
    try:
        assert _wait(lambda: calls == [("sub", ["AAA", "BBB"])]), calls
        assert w.stats()["reconciled"] >= 1
        assert w.stats()["pending_intents"] == 0
    finally:
        assert w.stop() is True


def test_rejected_unsubscribe_is_reconciled_and_coalesced():
    w = AngelSubWorker(max_pending=1, logger=lambda m: None, reconcile_interval=0.05)
    calls = []
    def unsubscribe_tickers(tickers):
        calls.append(("unsub", sorted(tickers)))
    gate = threading.Event()
    assert w.submit(gate.wait, 3) is True
    assert w.submit(unsubscribe_tickers, ["CCC"]) is False
    assert w.submit(unsubscribe_tickers, ["CCC", "DDD"]) is False
    assert w.stats()["pending_intents"] == 2, w.stats()   # coalesced per ticker
    gate.set()
    try:
        assert _wait(lambda: any(c[0] == "unsub" for c in calls)), calls
        assert calls[-1][1] == ["CCC", "DDD"], calls
        assert w.stats()["pending_intents"] == 0
    finally:
        assert w.stop() is True


def test_main_call_sites_use_the_shared_worker():
    src = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8", errors="replace")
    assert src.count("angel_sub_worker.shared.submit(") == 6
    assert "threading.Thread(target=self._angel." not in src