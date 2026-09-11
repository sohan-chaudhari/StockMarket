"""Phase 5 tests: _news_ticker_cache MAX_NEWS_CACHE_ENTRIES hard cap + LRU eviction.

Does NOT import main.py directly — main.py has expensive import-time side
effects (Angel One login, DB migrations, background threads; see the same
note in test_rate_limiting.py). Instead this exercises the identical
OrderedDict read/hit/write/evict pattern now used by
main.py's `_news_ticker_cache` (declaration ~line 505, read ~line 2893,
write ~line 2936) in isolation, so the eviction/TTL logic itself is proven
correct without paying that import cost.
"""
import time
import unittest
from collections import OrderedDict


class NewsTickerCache:
    """Mirrors main.py's _news_ticker_cache read/hit/write/evict logic exactly."""

    TTL = 180
    MAX_ENTRIES = 500

    def __init__(self):
        self.cache: "OrderedDict[str, dict]" = OrderedDict()

    def get(self, ticker: str, now: float = None):
        now = time.time() if now is None else now
        cached = self.cache.get(ticker)
        if cached and (now - cached["ts"]) < self.TTL:
            self.cache.move_to_end(ticker)
            return cached["data"]
        return None

    def set(self, ticker: str, data: dict, now: float = None):
        now = time.time() if now is None else now
        self.cache.pop(ticker, None)
        self.cache[ticker] = {"data": data, "ts": now}
        while len(self.cache) > self.MAX_ENTRIES:
            self.cache.popitem(last=False)


class TestNewsTickerCacheMaxEntries(unittest.TestCase):
    def setUp(self):
        self.cache = NewsTickerCache()

    def test_normal_insertion(self):
        self.cache.set("RELIANCE", {"articles": []})
        self.assertIn("RELIANCE", self.cache.cache)

    def test_cache_hit(self):
        data = {"articles": [{"title": "x"}]}
        self.cache.set("RELIANCE", data)
        self.assertEqual(self.cache.get("RELIANCE"), data)

    def test_cache_miss_unknown_ticker(self):
        self.assertIsNone(self.cache.get("UNKNOWN"))

    def test_ttl_expiration_still_works(self):
        now = time.time()
        self.cache.set("RELIANCE", {"articles": []}, now=now)
        # 181 seconds later — past the 180s TTL
        self.assertIsNone(self.cache.get("RELIANCE", now=now + 181))

    def test_ttl_not_expired_within_window(self):
        now = time.time()
        self.cache.set("RELIANCE", {"articles": []}, now=now)
        self.assertIsNotNone(self.cache.get("RELIANCE", now=now + 179))

    def test_max_size_enforced(self):
        for i in range(self.cache.MAX_ENTRIES + 200):
            self.cache.set(f"T{i}", {"articles": []})
        self.assertLessEqual(len(self.cache.cache), self.cache.MAX_ENTRIES)

    def test_eviction_removes_oldest(self):
        for i in range(self.cache.MAX_ENTRIES):
            self.cache.set(f"T{i}", {"articles": []})
        self.assertIn("T0", self.cache.cache)
        self.cache.set("OVERFLOW", {"articles": []})
        self.assertNotIn("T0", self.cache.cache)

    def test_newest_entry_survives_overflow(self):
        for i in range(self.cache.MAX_ENTRIES + 10):
            self.cache.set(f"T{i}", {"articles": [{"i": i}]})
        last = f"T{self.cache.MAX_ENTRIES + 9}"
        self.assertEqual(self.cache.get(last), {"articles": [{"i": self.cache.MAX_ENTRIES + 9}]})

    def test_lru_promotion_on_hit_protects_from_eviction(self):
        for i in range(self.cache.MAX_ENTRIES):
            self.cache.set(f"T{i}", {"articles": []})
        self.cache.get("T0")  # promote T0 to MRU
        self.cache.set("OVERFLOW", {"articles": []})
        self.assertIn("T0", self.cache.cache)
        self.assertNotIn("T1", self.cache.cache)

    def test_ttl_and_size_interact_correctly(self):
        now = time.time()
        self.cache.set("KEEPER", {"articles": []}, now=now)
        for i in range(self.cache.MAX_ENTRIES):
            self.cache.set(f"T{i}", {"articles": []}, now=now)
        # KEEPER was the first insert (now LRU-oldest) — size cap evicts it
        # independently of TTL, which hasn't expired yet.
        self.assertIsNone(self.cache.get("KEEPER", now=now + 1))
        self.assertLessEqual(len(self.cache.cache), self.cache.MAX_ENTRIES)

    def test_no_response_shape_regression(self):
        payload = {"articles": [{"title": "x", "summary": "y", "sentiment": "positive",
                                  "source": "Live Market Data", "url": "", "published_at": "2026-09-02"}]}
        self.cache.set("RELIANCE", payload)
        self.assertEqual(self.cache.get("RELIANCE"), payload)


if __name__ == "__main__":
    unittest.main()
