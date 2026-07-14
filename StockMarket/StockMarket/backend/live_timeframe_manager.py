"""
LiveTimeframeManager
=====================
Manages in-memory higher-timeframe builders (15m/30m/1h/4h/1D/1W/1M).

Key rules:
  - Built ONLY from completed 5m candles (via candle.5m.completed event).
  - Updated on EVERY tick (via candle.5m.updated event) — but only for TFs with viewer_count > 0.
  - NEVER written to DB.
  - Reference-counted: shared across all users viewing the same (ticker, tf).
  - Idle timeout: destroyed 2 minutes after last viewer leaves.
  - Upper bound: MAX_BUILDERS = 5000; LRU eviction when exceeded.
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

from config.timeframe_registry import TIMEFRAME_REGISTRY
from event_bus import event_bus


@dataclass
class LiveTfBuilder:
    ticker: str
    timeframe: str
    viewer_count: int = 0
    last_viewed: float = 0.0
    forming_candle: Optional[Dict] = None
    completed_candles: List[Dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_5m_volume: int = 0

    def update_from_5m(self, forming_5m: Dict):
        if self.viewer_count == 0:
            return
        if self.forming_candle is None:
            self.forming_candle = {
                "time": forming_5m.get("time", 0),
                "open": forming_5m.get("open", 0),
                "high": forming_5m.get("high", 0),
                "low": forming_5m.get("low", 0),
                "close": forming_5m.get("close", 0),
                "volume": forming_5m.get("volume", 0),
            }
            self._last_5m_volume = forming_5m.get("volume", 0)
        else:
            with self._lock:
                c = self.forming_candle
                c["high"] = max(c["high"], forming_5m.get("high", 0))
                c["low"] = min(c["low"], forming_5m.get("low", 0))
                c["close"] = forming_5m.get("close", c["close"])
                
                vol = forming_5m.get("volume", 0)
                if vol < self._last_5m_volume:
                    delta = vol
                else:
                    delta = vol - self._last_5m_volume
                self._last_5m_volume = vol
                c["volume"] = c.get("volume", 0) + delta

    def push_completed_5m(self, completed_5m: Dict):
        with self._lock:
            if self.forming_candle is None:
                self.forming_candle = {
                    "time": completed_5m.get("time", 0),
                    "open": completed_5m.get("open", 0),
                    "high": completed_5m.get("high", 0),
                    "low": completed_5m.get("low", 0),
                    "close": completed_5m.get("close", 0),
                    "volume": completed_5m.get("volume", 0),
                }
            else:
                self.completed_candles.append(dict(self.forming_candle))
                if len(self.completed_candles) > 200:
                    self.completed_candles = self.completed_candles[-100:]
                self.forming_candle = {
                    "time": completed_5m.get("time", 0),
                    "open": completed_5m.get("open", 0),
                    "high": completed_5m.get("high", 0),
                    "low": completed_5m.get("low", 0),
                    "close": completed_5m.get("close", 0),
                    "volume": completed_5m.get("volume", 0),
                }

    def initialize_from_candles(self, recent_5m: List[Dict]):
        with self._lock:
            self.completed_candles = []
            self.forming_candle = None
            for c in recent_5m:
                if self.forming_candle is None:
                    self.forming_candle = {
                        "time": c.get("time") or self._ts_from_dt(c.get("timestamp", datetime.now())),
                        "open": c.get("open", 0),
                        "high": c.get("high", 0),
                        "low": c.get("low", 0),
                        "close": c.get("close", 0),
                        "volume": c.get("volume", 0),
                    }
                else:
                    self.completed_candles.append(dict(self.forming_candle))
                    self.forming_candle = {
                        "time": c.get("time") or self._ts_from_dt(c.get("timestamp", datetime.now())),
                        "open": c.get("open", 0),
                        "high": c.get("high", 0),
                        "low": c.get("low", 0),
                        "close": c.get("close", 0),
                        "volume": c.get("volume", 0),
                    }

    def get_snapshot(self) -> Optional[Dict]:
        return {
            "forming": self.forming_candle,
            "completed": self.completed_candles[-50:] if self.completed_candles else [],
            "viewer_count": self.viewer_count,
        }

    @staticmethod
    def _ts_from_dt(dt) -> int:
        if isinstance(dt, datetime):
            return int(dt.timestamp())
        return int(dt) if isinstance(dt, (int, float)) else 0


class LiveTimeframeManager:
    MAX_BUILDERS = 5000
    IDLE_TIMEOUT_SEC = 120

    def __init__(self):
        self._builders: Dict[str, Dict[str, LiveTfBuilder]] = {}
        self._lock = threading.Lock()
        self._cleanup_thread_running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        event_bus.on("candle.5m.updated", self._on_5m_updated)
        event_bus.on("candle.5m.completed", self._on_5m_completed)
        event_bus.on("recovery.complete", self._on_recovery_complete)

    def add_viewer(self, ticker: str, timeframe: str):
        if timeframe not in TIMEFRAME_REGISTRY:
            return
        with self._lock:
            total = sum(len(tfs) for tfs in self._builders.values())
            if total >= self.MAX_BUILDERS:
                self._evict_lru()
            tfs = self._builders.setdefault(ticker, {})
            if timeframe not in tfs:
                tfs[timeframe] = LiveTfBuilder(ticker=ticker, timeframe=timeframe)
            tfs[timeframe].viewer_count += 1
            tfs[timeframe].last_viewed = time.time()

    def remove_viewer(self, ticker: str, timeframe: str):
        with self._lock:
            tfs = self._builders.get(ticker)
            if tfs is None:
                return
            builder = tfs.get(timeframe)
            if builder is None:
                return
            builder.viewer_count = max(0, builder.viewer_count - 1)
            builder.last_viewed = time.time()

    def get_current(self, ticker: str, timeframe: str) -> Optional[Dict]:
        with self._lock:
            tfs = self._builders.get(ticker)
            if tfs is None:
                return None
            builder = tfs.get(timeframe)
            if builder is None:
                return None
            return builder.get_snapshot()

    def get_all_active(self) -> Dict[str, List[str]]:
        with self._lock:
            return {
                t: list(tfs.keys())
                for t, tfs in self._builders.items()
                if any(b.viewer_count > 0 for b in tfs.values())
            }

    def builder_exists(self, ticker: str, timeframe: str) -> bool:
        with self._lock:
            tfs = self._builders.get(ticker)
            return tfs is not None and timeframe in tfs

    def _on_5m_updated(self, ticker: str, forming_candle: Dict):
        tfs = self._get_tfs_safe(ticker)
        for tf, builder in tfs.items():
            if builder.viewer_count > 0:
                builder.update_from_5m(forming_candle)

    def _on_5m_completed(self, ticker: str, candle: Dict):
        tfs = self._get_tfs_safe(ticker)
        candle_event = {
            "time": candle.get("timestamp"),
            "open": candle.get("open", 0),
            "high": candle.get("high", 0),
            "low": candle.get("low", 0),
            "close": candle.get("close", 0),
            "volume": candle.get("volume", 0),
            "timestamp": candle.get("timestamp"),
        }
        if candle_event["time"] and hasattr(candle_event["time"], "timestamp"):
            candle_event["time"] = int(candle_event["time"].timestamp())
        for tf, builder in tfs.items():
            if builder.viewer_count > 0:
                builder.push_completed_5m(candle_event)

    def _on_recovery_complete(self, ticker: str, recent_5m_buffer: List[Dict]):
        tfs = self._get_tfs_safe(ticker)
        for tf, builder in tfs.items():
            if builder.viewer_count > 0:
                builder.initialize_from_candles(recent_5m_buffer)

    def _get_tfs_safe(self, ticker: str) -> Dict[str, LiveTfBuilder]:
        with self._lock:
            tfs = self._builders.get(ticker)
            if tfs is None:
                return {}
            return dict(tfs)

    def _evict_lru(self):
        candidates = []
        for t, tfs in self._builders.items():
            for tf, b in tfs.items():
                if b.viewer_count == 0:
                    candidates.append((t, tf, b.last_viewed))
        if candidates:
            candidates.sort(key=lambda x: x[2])
            ticker, tf, _ = candidates[0]
            del self._builders[ticker][tf]
            if not self._builders[ticker]:
                del self._builders[ticker]

    def _cleanup_loop(self):
        while self._cleanup_thread_running:
            time.sleep(15)
            now = time.time()
            with self._lock:
                to_remove = []
                for t, tfs in list(self._builders.items()):
                    for tf, b in list(tfs.items()):
                        if b.viewer_count == 0 and (now - b.last_viewed) > self.IDLE_TIMEOUT_SEC:
                            to_remove.append((t, tf))
                for t, tf in to_remove:
                    if t in self._builders and tf in self._builders[t]:
                        del self._builders[t][tf]
                    if t in self._builders and not self._builders[t]:
                        del self._builders[t]

    def get_stats(self) -> dict:
        with self._lock:
            total_builders = sum(len(tfs) for tfs in self._builders.values())
            total_viewers = sum(
                b.viewer_count for tfs in self._builders.values() for b in tfs.values()
            )
            active = sum(
                1 for tfs in self._builders.values() for b in tfs.values() if b.viewer_count > 0
            )
            idle = total_builders - active
            return {
                "total_builders": total_builders,
                "active_builders": active,
                "idle_builders": idle,
                "total_viewers": total_viewers,
                "tickers_tracked": len(self._builders),
                "max_builders": self.MAX_BUILDERS,
            }


live_timeframe_manager = LiveTimeframeManager()
