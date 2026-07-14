import unittest
import threading
import time
from event_bus import EventBus


class TestEventBus(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()

    def test_on_sync_handler(self):
        results = []
        def handler(ticker, **kw):
            results.append(ticker)
        self.bus.on("test.event", handler)
        self.bus.emit("test.event", ticker="RELIANCE")
        self.assertEqual(results, ["RELIANCE"])

    def test_on_async_handler_registered(self):
        import asyncio
        results = []
        async def handler(ticker, **kw):
            results.append(ticker)
        loop = asyncio.new_event_loop()
        self.bus.set_loop(loop)
        self.bus.on("test.async", handler)
        # Verify async handler is registered
        with self.bus._lock:
            self.assertIn("v1.test.async", self.bus._async_handlers)
            self.assertEqual(len(self.bus._async_handlers["v1.test.async"]), 1)
        loop.close()

    def test_off_removes_handler(self):
        results = []
        def handler(ticker, **kw):
            results.append(ticker)
        self.bus.on("test.event", handler)
        self.bus.off("test.event", handler)
        self.bus.emit("test.event", ticker="RELIANCE")
        self.assertEqual(results, [])

    def test_emit_only_correct_event(self):
        results = []
        def h1(**kw):
            results.append("a")
        def h2(**kw):
            results.append("b")
        self.bus.on("event.a", h1)
        self.bus.on("event.b", h2)
        self.bus.emit("event.a")
        self.assertEqual(results, ["a"])

    def test_emit_with_data(self):
        results = {}
        def handler(**kw):
            results.update(kw)
        self.bus.on("data.event", handler)
        self.bus.emit("data.event", ticker="RELIANCE", price=2500)
        self.assertEqual(results, {"ticker": "RELIANCE", "price": 2500})

    def test_multiple_handlers_same_event(self):
        results = []
        self.bus.on("evt", lambda **kw: results.append(1))
        self.bus.on("evt", lambda **kw: results.append(2))
        self.bus.emit("evt")
        self.assertEqual(len(results), 2)
        self.assertIn(1, results)
        self.assertIn(2, results)

    def test_handler_exception_does_not_block(self):
        results = []
        def bad_handler(**kw):
            raise ValueError("oops")
        def good_handler(**kw):
            results.append("ok")
        self.bus.on("evt", bad_handler)
        self.bus.on("evt", good_handler)
        self.bus.emit("evt")
        self.assertEqual(results, ["ok"])

    def test_event_prefixed_automatically(self):
        results = []
        def handler(**kw):
            results.append(kw.get("ticker"))
        self.bus.on("test.prefix", handler)
        self.bus.emit("test.prefix", ticker="RELIANCE")
        self.assertEqual(results, ["RELIANCE"])

    def test_explicit_v1_event_not_double_prefixed(self):
        results = []
        def handler(**kw):
            results.append("ok")
        self.bus.on("v1.custom.event", handler)
        self.bus.emit("v1.custom.event")
        self.assertEqual(results, ["ok"])

    def test_thread_safety(self):
        results = []
        lock = threading.Lock()
        def handler(**kw):
            with lock:
                results.append(kw.get("i"))
        self.bus.on("thread.event", handler)
        threads = []
        for i in range(50):
            t = threading.Thread(target=lambda i=i: self.bus.emit("thread.event", i=i))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 50)

    def test_subscriber_count(self):
        def h1(**kw): pass
        def h2(**kw): pass
        self.assertEqual(self.bus.subscriber_count(), 0)
        self.bus.on("a", h1)
        self.bus.on("b", h2)
        self.assertEqual(self.bus.subscriber_count(), 2)

    def test_status_property(self):
        self.assertEqual(self.bus.status, "healthy")


if __name__ == "__main__":
    unittest.main()
