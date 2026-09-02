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
  - Hard upper bound: MAX_BUILDERS = 5000. LRU eviction runs first; if nothing
    is evictable (every builder actively viewed), add_viewer() returns False
    and creates nothing rather than exceeding the cap — callers already treat
    a missing builder as "fall back to on-demand resampling" (see add_viewer).

Bucket Snapping (NSE Session Aligned)
---------------------------------------
NSE session starts at 09:15 IST.  All higher-TF buckets are aligned to this
session start, NOT to midnight.

  15m  → 09:15, 09:30, 09:45, 10:00 …  (no offset needed: 555 % 15 == 0)
  30m  → 09:15, 09:45, 10:15, 10:45 …  (offset='15min' in pandas)
  1h   → 09:15, 10:15, 11:15, 12:15 …  (offset='15min' in pandas)

This module implements the snapping independently so that LiveTfBuilder
can aggregate 5m candles into correctly-aligned buckets without relying on
pandas (which cannot be used in a hot tick path).
"""

import time
import threading
from datetime import datetime, timedelta, date as _date
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

from config.timeframe_registry import TIMEFRAME_REGISTRY
from event_bus import event_bus

# ── NSE session constants ──────────────────────────────────────────────────
_IST_OFFSET_SEC = int(5.5 * 3600)   # 19800 s
_NSE_OPEN_SEC   = 9 * 3600 + 15 * 60  # 33300 s from IST midnight

# Lazy reference to nse_calendar — imported once at first use so the
# circular-import risk (exchange_calendar → nothing; live_timeframe_manager
# → exchange_calendar) is avoided at module load time.
_nse_calendar = None

def _get_session_open_sec(ist_midnight_utc: int) -> int:
    """Return the IST open-of-session in seconds-from-midnight for a given
    UTC epoch of IST midnight.  Consults nse_calendar for special sessions
    (e.g. Muhurat trading at 18:00 IST instead of 09:15) and falls back to
    the standard _NSE_OPEN_SEC when none is registered.
    LTM-01: was previously a hardcoded constant, causing wrong bucket
    alignment on every special-session day.
    """
    global _nse_calendar
    if _nse_calendar is None:
        try:
            from exchange_calendar import nse_calendar as _cal
            _nse_calendar = _cal
        except Exception:
            return _NSE_OPEN_SEC

    # Derive IST calendar date from ist_midnight_utc
    # ist_midnight_utc + _IST_OFFSET_SEC = seconds since epoch at IST midnight
    ist_day_num = (ist_midnight_utc + _IST_OFFSET_SEC) // 86400
    ist_dt = _date(1970, 1, 1) + timedelta(days=ist_day_num)

    special = _nse_calendar._special_sessions.get(ist_dt)
    if special is not None:
        return special.open_time.hour * 3600 + special.open_time.minute * 60
    return _NSE_OPEN_SEC


def _snap_to_nse_bucket(epoch_sec: int, tf_minutes: int) -> int:
    """
    Return the epoch second of the NSE-session-aligned higher-TF bucket that
    contains *epoch_sec*.

    Respects special sessions (e.g. Muhurat trading) by consulting
    nse_calendar instead of always using the hardcoded 09:15 anchor.

    Example for 30m on a normal day:
        09:15 → 09:15   (first bucket)
        09:44 → 09:15
        09:45 → 09:45   (second bucket)
    """
    if tf_minutes <= 0:
        return epoch_sec
    bucket_sec = tf_minutes * 60
    # IST midnight in UTC epoch seconds
    ist_midnight_utc = ((epoch_sec + _IST_OFFSET_SEC) // 86400) * 86400 - _IST_OFFSET_SEC
    open_sec = _get_session_open_sec(ist_midnight_utc)
    session_start = ist_midnight_utc + open_sec
    offset = epoch_sec - session_start
    if offset < 0:
        return session_start
    return session_start + (offset // bucket_sec) * bucket_sec


# ── TF → minutes mapping ────────────────────────────────────────────────────
_TF_MINUTES: Dict[str, int] = {
    '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240,
}


# ── LiveTfBuilder ────────────────────────────────────────────────────────────

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
    _tf_minutes: int = 5   # set in __post_init__

    def __post_init__(self):
        self._tf_minutes = _TF_MINUTES.get(self.timeframe, 5)

    # ── internal helpers ────────────────────────────────────────────────────

    def _get_bucket(self, candle: Dict) -> int:
        """Return the NSE-aligned bucket epoch for this candle's timestamp."""
        t = candle.get("time", 0)
        if isinstance(t, (int, float)) and t > 1e9:
            return _snap_to_nse_bucket(int(t), self._tf_minutes)
        return 0

    def _new_forming(self, source_5m: Dict, bucket_time: int) -> Dict:
        return {
            "time":   bucket_time,
            "open":   source_5m.get("open",   0),
            "high":   source_5m.get("high",   0),
            "low":    source_5m.get("low",    0),
            "close":  source_5m.get("close",  0),
            "volume": source_5m.get("volume", 0),
        }

    def _aggregate_into(self, forming: Dict, source_5m: Dict):
        """Merge source_5m into the existing forming candle (same bucket)."""
        forming["high"]  = max(forming["high"],  source_5m.get("high",  forming["high"]))
        forming["low"]   = min(forming["low"],   source_5m.get("low",   forming["low"]))
        forming["close"] = source_5m.get("close", forming["close"])
        forming["volume"] = forming.get("volume", 0) + source_5m.get("volume", 0)

    # ── public API ──────────────────────────────────────────────────────────

    def update_from_5m(self, forming_5m: Dict):
        """
        Called on every live tick.  Updates the forming higher-TF candle.
        If the 5m bucket has moved to a new higher-TF bucket, we start a fresh
        forming candle (the old one will be pushed to completed when its 5m
        candle fires the completion event).
        """
        if self.viewer_count == 0:
            return
        bucket_time = self._get_bucket(forming_5m)
        if bucket_time == 0:
            return

        with self._lock:
            if self.forming_candle is None:
                self.forming_candle = self._new_forming(forming_5m, bucket_time)
                self._last_5m_volume = forming_5m.get("volume", 0)
                return

            if self.forming_candle["time"] == bucket_time:
                # Same bucket → update OHLCV live
                c = self.forming_candle
                c["high"]  = max(c["high"],  forming_5m.get("high",  c["high"]))
                c["low"]   = min(c["low"],   forming_5m.get("low",   c["low"]))
                c["close"] = forming_5m.get("close", c["close"])
                vol = forming_5m.get("volume", 0)
                delta = vol - self._last_5m_volume if vol >= self._last_5m_volume else vol
                self._last_5m_volume = vol
                c["volume"] = c.get("volume", 0) + delta
            else:
                # New bucket started — forming candle will be pushed to completed
                # when push_completed_5m fires.  Until then keep the previous
                # forming in place so the UI doesn't flash blank.
                # We do NOT push here to avoid double-counting.
                pass

    def push_completed_5m(self, completed_5m: Dict):
        """
        Called when a 5m candle is finalized.

        Correct higher-TF aggregation:
          • If the completed 5m belongs to the CURRENT higher-TF bucket
            → merge it into the existing forming candle.
          • If the completed 5m belongs to a NEW higher-TF bucket
            → push the current forming to completed[], start a new forming.
        """
        bucket_time = self._get_bucket(completed_5m)
        if bucket_time == 0:
            return

        with self._lock:
            if self.forming_candle is None:
                # First ever candle for this builder
                self.forming_candle = self._new_forming(completed_5m, bucket_time)
                self._last_5m_volume = 0
                return

            if self.forming_candle["time"] == bucket_time:
                # Same higher-TF bucket: aggregate the completed 5m into forming
                self._aggregate_into(self.forming_candle, completed_5m)
            else:
                # New higher-TF bucket: push current forming → completed[]
                self.completed_candles.append(dict(self.forming_candle))
                if len(self.completed_candles) > 200:
                    self.completed_candles = self.completed_candles[-100:]
                # Start a fresh forming candle for the new bucket
                self.forming_candle = self._new_forming(completed_5m, bucket_time)
                self._last_5m_volume = 0

    def initialize_from_candles(self, recent_5m: List[Dict]):
        """
        Seed the builder from a list of recent 5m candles.
        Groups them by higher-TF bucket and aggregates correctly.
        """
        with self._lock:
            self.completed_candles = []
            self.forming_candle = None
            buckets: Dict[int, Dict] = {}
            for c in recent_5m:
                t = c.get("time") or self._ts_from_dt(c.get("timestamp", datetime.now()))
                if isinstance(t, (int, float)) and t > 1e9:
                    bt = _snap_to_nse_bucket(int(t), self._tf_minutes)
                else:
                    continue
                if bt not in buckets:
                    buckets[bt] = {
                        "time":   bt,
                        "open":   c.get("open",   0),
                        "high":   c.get("high",   0),
                        "low":    c.get("low",    0),
                        "close":  c.get("close",  0),
                        "volume": c.get("volume", 0),
                    }
                else:
                    b = buckets[bt]
                    b["high"]  = max(b["high"],  c.get("high",  b["high"]))
                    b["low"]   = min(b["low"],   c.get("low",   b["low"]))
                    b["close"] = c.get("close",  b["close"])
                    b["volume"] = b.get("volume", 0) + c.get("volume", 0)

            sorted_buckets = sorted(buckets.values(), key=lambda x: x["time"])
            if not sorted_buckets:
                return
            # All but the last are "completed"; last is "forming"
            self.completed_candles = sorted_buckets[:-1]
            self.forming_candle    = sorted_buckets[-1]

    def get_snapshot(self) -> Optional[Dict]:
        return {
            "forming":      self.forming_candle,
            "completed":    self.completed_candles[-50:] if self.completed_candles else [],
            "viewer_count": self.viewer_count,
        }

    @staticmethod
    def _ts_from_dt(dt) -> int:
        if isinstance(dt, datetime):
            return int(dt.timestamp())
        return int(dt) if isinstance(dt, (int, float)) else 0


# ── LiveTimeframeManager ─────────────────────────────────────────────────────

class LiveTimeframeManager:
    MAX_BUILDERS    = 5000
    IDLE_TIMEOUT_SEC = 120

    def __init__(self):
        self._builders: Dict[str, Dict[str, LiveTfBuilder]] = {}
        self._lock = threading.Lock()
        self._cleanup_thread_running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        event_bus.on("candle.5m.updated",    self._on_5m_updated)
        event_bus.on("candle.5m.completed",  self._on_5m_completed)
        event_bus.on("recovery.complete",    self._on_recovery_complete)

        # Decision 5: MAX_BUILDERS was a soft cap — if _evict_lru() found no
        # evictable candidate (all existing builders actively viewed), a new
        # one was created anyway with no bound. cap_hits counts how often the
        # hard cap below actually turns a viewer away, for observability.
        self._cap_hits = 0

    def add_viewer(self, ticker: str, timeframe: str) -> bool:
        """Register a viewer for (ticker, timeframe)'s in-memory higher-TF builder.

        Returns True if an active builder now backs this (ticker, timeframe) —
        either it already existed or one was just created. Returns False if
        MAX_BUILDERS is reached and nothing was evictable (every existing
        builder has active viewers) — in that case NO builder is created and
        the caller keeps None from get_current(), which every existing call
        site already treats as "no live in-memory data available" and falls
        back to the DB/on-the-fly-resample path (main.py's paginated intraday
        endpoint does this already). That resample fallback is the graceful
        degradation: bounded memory under a reconnect storm, correct-but-
        slower data for whichever viewer trips the cap, instead of either an
        unbounded builder count or a rejected request.
        """
        if timeframe not in TIMEFRAME_REGISTRY:
            return False
        with self._lock:
            tfs = self._builders.get(ticker, {})
            if timeframe in tfs:
                tfs[timeframe].viewer_count += 1
                tfs[timeframe].last_viewed   = time.time()
                return True

            total = sum(len(t) for t in self._builders.values())
            if total >= self.MAX_BUILDERS:
                self._evict_lru()
                total = sum(len(t) for t in self._builders.values())
                if total >= self.MAX_BUILDERS:
                    self._cap_hits += 1
                    print(f"[LiveTF] MAX_BUILDERS={self.MAX_BUILDERS} reached, no evictable "
                          f"candidate — declining new builder for {ticker}/{timeframe} "
                          f"(cap_hits={self._cap_hits}); caller falls back to on-demand resample")
                    return False

            tfs = self._builders.setdefault(ticker, {})
            tfs[timeframe] = LiveTfBuilder(ticker=ticker, timeframe=timeframe)
            tfs[timeframe].viewer_count += 1
            tfs[timeframe].last_viewed   = time.time()
            return True

    def remove_viewer(self, ticker: str, timeframe: str):
        with self._lock:
            tfs = self._builders.get(ticker)
            if tfs is None:
                return
            builder = tfs.get(timeframe)
            if builder is None:
                return
            builder.viewer_count = max(0, builder.viewer_count - 1)
            builder.last_viewed   = time.time()

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

    # ── event handlers ──────────────────────────────────────────────────────

    def _on_5m_updated(self, ticker: str, forming_candle: Dict):
        tfs = self._get_tfs_safe(ticker)
        for tf, builder in tfs.items():
            if builder.viewer_count > 0:
                builder.update_from_5m(forming_candle)

    def _on_5m_completed(self, ticker: str, candle: Dict):
        tfs = self._get_tfs_safe(ticker)
        candle_event = {
            "time":      candle.get("timestamp"),
            "open":      candle.get("open",   0),
            "high":      candle.get("high",   0),
            "low":       candle.get("low",    0),
            "close":     candle.get("close",  0),
            "volume":    candle.get("volume", 0),
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

    # ── internals ───────────────────────────────────────────────────────────

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
            self._sweep_idle_builders()

    def _sweep_idle_builders(self):
        """One cleanup pass: remove builders with no fresh request in
        IDLE_TIMEOUT_SEC. Factored out of _cleanup_loop so it can be called
        directly (deterministically, without waiting on the background
        thread) both here and from tests.

        REST callers (the only current callers of add_viewer) have no
        explicit "stop viewing" signal -- unlike a WebSocket disconnect, a
        paginated intraday GET request has no matching "unsubscribe".
        Without this sweep, viewer_count only ever increments and the
        "destroyed 2 minutes after last viewer leaves" contract documented
        on this class could never trigger, since eviction requires
        viewer_count == 0. last_viewed is already correctly refreshed on
        every add_viewer call, so staleness on it is the authoritative
        "no longer being viewed" signal.
        """
        now = time.time()
        with self._lock:
            to_remove = []
            for t, tfs in list(self._builders.items()):
                for tf, b in list(tfs.items()):
                    if (now - b.last_viewed) > self.IDLE_TIMEOUT_SEC:
                        b.viewer_count = 0
                        to_remove.append((t, tf))
            for t, tf in to_remove:
                if t in self._builders and tf in self._builders[t]:
                    del self._builders[t][tf]
                if t in self._builders and not self._builders[t]:
                    del self._builders[t]

    def get_stats(self) -> dict:
        with self._lock:
            total_builders = sum(len(tfs) for tfs in self._builders.values())
            total_viewers  = sum(
                b.viewer_count for tfs in self._builders.values() for b in tfs.values()
            )
            active = sum(
                1 for tfs in self._builders.values() for b in tfs.values() if b.viewer_count > 0
            )
            idle = total_builders - active
            return {
                "total_builders":  total_builders,
                "active_builders": active,
                "idle_builders":   idle,
                "total_viewers":   total_viewers,
                "tickers_tracked": len(self._builders),
                "max_builders":    self.MAX_BUILDERS,
                "cap_hits":        self._cap_hits,
            }


live_timeframe_manager = LiveTimeframeManager()
