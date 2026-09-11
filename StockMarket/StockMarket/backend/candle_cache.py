"""
CandleCache
============
Multi-level cache for chart data.

  L1: LiveTimeframeManager (real-time forming + recent completed candles)
  L2: In-memory resampled candle cache (TTL-based)
  L3: Database (historical stored candles)

Key rules:
  - L1 is always authoritative for live data (never stale).
  - L2 has TTL: 5 min intraday, 1h after market, 6h weekends.
  - L3 is always fallback.
  - Cache invalidation: on candle.5m.completed for related TFs.
"""

import time
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from config.timeframe_registry import TIMEFRAME_REGISTRY

# IST offset — matches database.get_ist_now_aware(); defined here to avoid circular import
_IST = timezone(timedelta(hours=5, minutes=30))


class CacheEntry:
    __slots__ = ('key', 'data', 'expires_at', 'created_at', 'hits')

    def __init__(self, key: str, data: List[Dict], ttl_sec: int):
        self.key = key
        self.data = data
        self.expires_at = time.time() + ttl_sec
        self.created_at = time.time()
        self.hits = 0

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def hit(self):
        self.hits += 1


class CandleCache:
    # Hard cap alongside the existing TTL, so L2 stays bounded even under a
    # request pattern that keeps generating fresh keys faster than TTL expires
    # them (e.g. many distinct start/end range queries). Same pattern as
    # LiveTimeframeManager.MAX_BUILDERS.
    MAX_L2_ENTRIES = 5000

    def __init__(self, live_timeframe_manager=None):
        # OrderedDict so eviction (oldest-first) and LRU promotion (move_to_end)
        # are both O(1) — no scanning the cache on every read/write.
        self._l2: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._lock = threading.Lock()
        self._live_mgr = live_timeframe_manager
        self._l1_hits = 0
        self._l2_hits = 0
        self._l3_hits = 0
        self._misses = 0
        self._l2_evictions = 0
        self._cleanup_thread_running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def get(self, ticker: str, timeframe: str, start: Optional[int] = None,
            end: Optional[int] = None) -> Optional[List[Dict]]:
        cache_key = f"{ticker}:{timeframe}:{start}:{end}"

        result = self._try_l1(ticker, timeframe, start, end)
        if result is not None:
            self._l1_hits += 1
            return result

        result = self._try_l2(cache_key, timeframe)
        if result is not None:
            self._l2_hits += 1
            return result

        self._misses += 1
        return None

    def set(self, ticker: str, timeframe: str, data: List[Dict],
            ttl_sec: Optional[int] = None, start: Optional[int] = None,
            end: Optional[int] = None):
        if ttl_sec is None:
            ttl_sec = self._default_ttl(timeframe)
        cache_key = f"{ticker}:{timeframe}:{start}:{end}"
        with self._lock:
            # Overwriting an existing key leaves its position unchanged in an
            # OrderedDict, so drop it first — the fresh insert below both
            # updates the value and moves it to the MRU (end) position.
            self._l2.pop(cache_key, None)
            self._l2[cache_key] = CacheEntry(cache_key, data, ttl_sec)
            while len(self._l2) > self.MAX_L2_ENTRIES:
                self._l2.popitem(last=False)  # O(1): evict the LRU entry
                self._l2_evictions += 1

    def invalidate(self, ticker: str, timeframe: str = None):
        prefix = f"{ticker}:"
        with self._lock:
            keys = list(self._l2.keys())
            for k in keys:
                if k.startswith(prefix):
                    if timeframe is None or f":{timeframe}:" in k:
                        del self._l2[k]

    def _try_l1(self, ticker: str, tf: str, start: int, end: int) -> Optional[List[Dict]]:
        if self._live_mgr is None:
            return None
        snapshot = self._live_mgr.get_current(ticker, tf)
        if snapshot is None:
            return None
        result = list(snapshot.get("completed", []))
        forming = snapshot.get("forming")
        if forming:
            result.append(forming)
        if start and result:
            result = [c for c in result if (c.get("time", 0) or 0) >= start]
        if end and result:
            result = [c for c in result if (c.get("time", 0) or 0) <= end]
        return result if result else None

    def _try_l2(self, key: str, tf: str) -> Optional[List[Dict]]:
        with self._lock:
            entry = self._l2.get(key)
            if entry is not None and not entry.is_expired():
                entry.hit()
                self._l2.move_to_end(key)  # O(1): mark as most-recently-used
                return list(entry.data)
            if entry is not None and entry.is_expired():
                del self._l2[key]
        return None

    def _default_ttl(self, timeframe: str) -> int:
        now = datetime.now(_IST)  # always IST, server-timezone-independent
        if now.weekday() >= 5:
            return 21600
        t = now.time()
        if t >= now.replace(hour=9, minute=15).time() and t <= now.replace(hour=15, minute=30).time():
            return 300
        return 3600

    def _cleanup_loop(self):
        while self._cleanup_thread_running:
            time.sleep(60)
            with self._lock:
                expired = [k for k, v in self._l2.items() if v.is_expired()]
                for k in expired:
                    del self._l2[k]

    def get_stats(self) -> dict:
        total = self._l1_hits + self._l2_hits + self._l3_hits + self._misses
        hit_rate = round((self._l1_hits + self._l2_hits + self._l3_hits) / max(total, 1) * 100, 1)
        with self._lock:
            l2_size = len(self._l2)
        return {
            "l1_hits": self._l1_hits,
            "l2_hits": self._l2_hits,
            "l3_hits": self._l3_hits,
            "misses": self._misses,
            "hit_rate_pct": hit_rate,
            "l2_entries": l2_size,
            "l2_max_entries": self.MAX_L2_ENTRIES,
            "l2_evictions": self._l2_evictions,
            "total_requests": total,
        }


candle_cache = CandleCache()
