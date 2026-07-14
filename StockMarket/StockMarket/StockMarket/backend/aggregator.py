"""
Live Candle Aggregator
======================
Processes raw ticks and forms 1m, 5m, 15m, 30m, 1h candles in real-time.

Architecture:
  Ticks → 1m candle (direct from ticks)
  1m    → 5m, 15m, 30m, 1h (via aggregation when 1m completes)

Key features:
  - NSE session-aligned bucketing (09:15 IST start)
  - Tick ordering validation (ignores stale/late ticks)
  - OHLC validation + auto-fix on every read
  - Completed candles persisted to `candles` table
  - Market-hours + holiday check
  - Crash recovery: reloads last candle from DB at startup
"""

import time
import threading
import copy
from datetime import datetime, timedelta, timezone, date
from typing import Dict, Optional, List, Tuple, Callable

IST = timezone(timedelta(hours=5, minutes=30))

NSE_OPEN_HOUR = 9
NSE_OPEN_MIN = 15
NSE_CLOSE_HOUR = 15
NSE_CLOSE_MIN = 30
NSE_CLOSE_GRACE_MIN = 15  # accept final closing ticks up to 15:45

_NSE_OPEN_SEC = NSE_OPEN_HOUR * 3600 + NSE_OPEN_MIN * 60       # 33300
_NSE_CLOSE_SEC = NSE_CLOSE_HOUR * 3600 + NSE_CLOSE_MIN * 60    # 55800
_NSE_CLOSE_GRACE_SEC = NSE_CLOSE_HOUR * 3600 + (NSE_CLOSE_MIN + NSE_CLOSE_GRACE_MIN) * 60  # 56700
_IST_OFFSET_SEC = 5 * 3600 + 30 * 60                           # 19800

# Aggregation chain: ticks → 1m → 5m → 15m → 30m → 1h → 1D → 1W → 1M
# - 1m built from ticks (real-time)
# - 5m/15m/30m/1h built when 1m completes (real-time)
# - 1D built when 1h completes (the trading day changes)
# - 1W/1M built from 1D (triggered by end-of-day batch or on demand)
TIMEFRAME_BUCKETS = [
    ("1m",  1),
    ("5m",  5),
    ("15m", 15),
    ("30m", 30),
    ("1h",  60),
    ("1D",  375),   # whole trading session
    ("1W",  375*5), # week (approx)
    ("1M",  375*22),# month (approx)
]
AGGREGATION_FACTOR = {
    "5m":  5,
    "15m": 15,
    "30m": 30,
    "1h":  60,
    "1D":  375,     # minutes in a trading day
    "1W":  375*5,   # ~5 trading days
    "1M":  375*22,  # ~22 trading days
}

# Chains: which TF feeds into which
AGGREGATION_CHAIN = [
    ("1m",  ["5m", "15m", "30m", "1h"]),
    ("1h",  ["1D"]),
    ("1D",  ["1W", "1M"]),
]


# ── helpers ──────────────────────────────────────────────────────────

def ist_now_naive() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def is_trading_day(d: date, holidays: set = None) -> bool:
    if d.weekday() >= 5:
        return False
    if holidays and d in holidays:
        return False
    return True


def is_market_hour(dt: datetime) -> bool:
    sec = dt.hour * 3600 + dt.minute * 60 + dt.second
    return _NSE_OPEN_SEC <= sec < _NSE_CLOSE_GRACE_SEC


def snap_to_nse_session(ts_epoch, bucket_minutes: int) -> int:
    """Snap a UTC epoch-second to the nearest NSE session-aligned bucket start (09:15 IST).
    Ticks during the post-market grace period (15:30-15:45) are snapped to the
    last valid bucket so they update the official closing candle.
    Always returns int to guarantee consistent timestamp type downstream.
    """
    ts_epoch = int(ts_epoch)  # ensure int — float input yields float // → float otherwise
    bucket_sec = bucket_minutes * 60
    ist_seconds = (ts_epoch + _IST_OFFSET_SEC) % 86400
    ist_day_start = ((ts_epoch + _IST_OFFSET_SEC) // 86400) * 86400 - _IST_OFFSET_SEC
    session_start = ist_day_start + _NSE_OPEN_SEC
    if ist_seconds < _NSE_OPEN_SEC:
        return session_start
    if ist_seconds >= _NSE_CLOSE_SEC:
        slots = (_NSE_CLOSE_SEC - _NSE_OPEN_SEC) // bucket_sec
        return session_start + (slots - 1) * bucket_sec
    offset = ts_epoch - session_start
    return session_start + (offset // bucket_sec) * bucket_sec


def validate_ohlc(o: float, h: float, l: float, c: float) -> Tuple[bool, str]:
    if h < o:
        return False, f"high ({h}) < open ({o})"
    if h < c:
        return False, f"high ({h}) < close ({c})"
    if l > o:
        return False, f"low ({l}) > open ({o})"
    if l > c:
        return False, f"low ({l}) > close ({c})"
    if h < l:
        return False, f"high ({h}) < low ({l})"
    return True, ""


def fix_ohlc(o: float, h: float, l: float, c: float) -> Tuple[float, float, float, float]:
    fixed_h = max(h, o, c)
    fixed_l = min(l, o, c)
    return o, fixed_h, fixed_l, c


# ── Aggregator ────────────────────────────────────────────────────────

class LiveCandleAggregator:
    """
    In-memory engine: ticks → 1m candles → higher TFs.

    Design decisions
    ----------------
    - *Tick ordering*: each tick carries a wall-clock timestamp; ticks older
      than the last-processed tick per ticker are discarded (with a log).
    - *Continuous build*: only 1m candles are built directly from ticks.
      When a 1m candle completes, it is aggregated into 5m/15m/30m/1h.
      This guarantees cross-timeframe consistency.
    - *Gap handling*: 1m candles that never receive a tick are **not** created.
      TradingView's `has_empty_bars: true` shows gaps where no trade occurred.
    - *Crash recovery*: call `recover_from_db()` after init to rehydrate the
      last completed 1m candle from the DB.
    """

    def __init__(self):
        # active_candles[ticker][timeframe] = {timestamp, open, high, low, close, volume}
        self.active_candles: Dict[str, Dict[str, Dict]] = {}
        self._completed_1m: Dict[str, list] = {}
        self._previous_closes: Dict[str, float] = {}
        self._last_tick_ts: Dict[str, float] = {}
        self._stale_skip_count: Dict[str, int] = {}
        self._last_known_bucket: Dict[str, Dict[str, int]] = {}
        # Batch flush: accumulates candles during a process_tick cycle,
        # then flushes them in the background so the tick processor is never blocked.
        self._flush_batch: List[Dict] = []
        self._flush_queue: List[List[Dict]] = []
        self._flush_queue_lock = threading.Lock()
        self._flush_retries: Dict[int, int] = {}  # id(batch) -> retry count
        self._flush_id_counter = 0
        self.batch_flush_callback: Optional[Callable[[List[Dict]], None]] = None
        self._holidays: set = set()
        self._lock = threading.Lock()
        self._last_activity: Dict[str, float] = {}  # ticker -> epoch of last tick
        self._ticker_ttl = 3600  # remove inactive tickers after 1h
        self._last_gc: float = 0.0  # last GC timestamp, rate-limited to once per 60s
        self._warned_suspicious_ts: set = set()
        self._warned_frozen_ts: set = set()
        # Background flush worker
        self._flush_worker_running = True
        self._flush_thread = threading.Thread(target=self._flush_worker, daemon=True)
        self._flush_thread.start()

    # ── public API ────────────────────────────────────────────────────

    def set_holidays(self, holidays: set):
        self._holidays = holidays

    def set_previous_close(self, ticker: str, price: float):
        self._previous_closes[ticker] = price

    def recover_from_db(self, db_session):
        """Reload last completed 1m candle + reconstruct forming higher-TF candles from DB.
        Call once at startup so the aggregator continues where it left off
        without losing intra-session price action after a crash/restart.
        """
        from models import Candle
        try:
            today_open = ist_now_naive().replace(hour=NSE_OPEN_HOUR, minute=NSE_OPEN_MIN, second=0, microsecond=0)
            # Only recover tickers with candles from today to avoid slow startup on 5000+ tickers
            tickers = [r[0] for r in db_session.query(Candle.ticker).distinct()
                .filter(Candle.timestamp >= today_open).all()]
            now_dt = ist_now_naive()

            for t in tickers:
                last = db_session.query(Candle).filter(
                    Candle.ticker == t,
                    Candle.timeframe == "1m",
                    Candle.is_completed == True,
                ).order_by(Candle.timestamp.desc()).first()
                if last:
                    self.set_previous_close(t, last.close)

                # ── Reconstruct higher-TF candles from today's 1m candles ──
                if not (last and last.timestamp >= today_open):
                    continue

                completed_1m = db_session.query(Candle).filter(
                    Candle.ticker == t,
                    Candle.timeframe == "1m",
                    Candle.is_completed == True,
                    Candle.timestamp >= today_open,
                ).order_by(Candle.timestamp.asc()).all()
                if not completed_1m:
                    continue

                self.active_candles[t] = {}
                self._completed_1m[t] = []
                self._last_known_bucket[t] = {}

                # Replay all completed 1m candles through the aggregation chain
                for cm in completed_1m:
                    cdict = {
                        "timestamp": cm.timestamp,
                        "open": cm.open, "high": cm.high,
                        "low": cm.low, "close": cm.close,
                        "volume": cm.volume,
                    }
                    self._completed_1m[t].append(cdict)
                    for tf in ["5m", "15m", "30m", "1h"]:
                        self._aggregate_intraday(t, tf, cdict)

                # Only init forming candles during market hours (avoids phantom after-hours state)
                if is_market_hour(now_dt):
                    now_epoch = int(now_dt.replace(tzinfo=IST).timestamp())
                    snapped_1m = snap_to_nse_session(now_epoch, 1)
                    snapped_1m_dt = datetime.fromtimestamp(snapped_1m, tz=IST).replace(tzinfo=None)
                    last_close = completed_1m[-1].close
                    self.active_candles[t]["1m"] = self._create_candle(snapped_1m_dt, last_close)
                    self._last_known_bucket[t]["1m"] = snapped_1m

                    # Init forming candles for higher intraday TFs (5m/15m/30m/1h)
                    for tf in ["5m", "15m", "30m", "1h"]:
                        bucket_min = AGGREGATION_FACTOR[tf]
                        snapped_tf = snap_to_nse_session(now_epoch, bucket_min)
                        snapped_tf_dt = datetime.fromtimestamp(snapped_tf, tz=IST).replace(tzinfo=None)
                        self.active_candles[t][tf] = self._create_candle(snapped_tf_dt, last_close)
                        self._last_known_bucket[t][tf] = snapped_tf

                # Reconstruct 1D candle from all today's 1m candles
                today_midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                d_open = completed_1m[0].open
                d_high = max(cm.high for cm in completed_1m)
                d_low = min(cm.low for cm in completed_1m)
                d_close = completed_1m[-1].close
                d_vol = sum(cm.volume for cm in completed_1m)
                forming_1d = {
                    "timestamp": today_midnight,
                    "open": d_open, "high": d_high, "low": d_low, "close": d_close,
                    "volume": d_vol,
                }
                self.active_candles[t]["1D"] = forming_1d
                self._last_known_bucket[t]["1D"] = today_midnight.toordinal()

                # 1W / 1M — combine DB-completed 1D candles with the forming day
                monday = today_midnight - timedelta(days=today_midnight.weekday())
                first_of_month = today_midnight.replace(day=1)

                def _extend_1d(candles, forming, ts_key):
                    # Merge DB candles (before today) + today's forming candle
                    all_days = []
                    for c in candles:
                        if c.timestamp < forming["timestamp"]:
                            all_days.append({
                                "open": c.open, "high": c.high, "low": c.low,
                                "close": c.close, "volume": c.volume,
                            })
                    all_days.append(forming)
                    if all_days:
                        return {
                            "timestamp": ts_key,
                            "open": all_days[0]["open"],
                            "high": max(c["high"] for c in all_days),
                            "low": min(c["low"] for c in all_days),
                            "close": all_days[-1]["close"],
                            "volume": sum(c["volume"] for c in all_days),
                        }
                    return None

                wk_1d = db_session.query(Candle).filter(
                    Candle.ticker == t,
                    Candle.timeframe == "1D",
                    Candle.is_completed == True,
                    Candle.timestamp >= monday,
                ).order_by(Candle.timestamp.asc()).all()
                wk = _extend_1d(wk_1d, forming_1d, monday)
                if wk:
                    self.active_candles[t]["1W"] = wk
                else:
                    self.active_candles[t]["1W"] = self._create_candle(monday, last_close)
                self._last_known_bucket[t]["1W"] = monday.toordinal()

                mo_1d = db_session.query(Candle).filter(
                    Candle.ticker == t,
                    Candle.timeframe == "1D",
                    Candle.is_completed == True,
                    Candle.timestamp >= first_of_month,
                ).order_by(Candle.timestamp.asc()).all()
                mo = _extend_1d(mo_1d, forming_1d, first_of_month)
                if mo:
                    self.active_candles[t]["1M"] = mo
                else:
                    self.active_candles[t]["1M"] = self._create_candle(first_of_month, last_close)
                self._last_known_bucket[t]["1M"] = first_of_month.toordinal()

            print(f"[Aggregator] Recovered {len(tickers)} tickers (forming candles reconstructed)")
        except Exception as e:
            print(f"[Aggregator] Recovery error: {e}")
            import traceback
            traceback.print_exc()

    def process_tick(self, ticker: str, price: float, volume: int = 0, tick_ts: float = None,
                     day_open: float = None, day_high: float = None, day_low: float = None) -> Dict[str, Dict]:
        """Process a single price tick.

        Args:
            tick_ts: Unix-epoch seconds of the tick (from broker). If None, uses
                     wall clock. Ticks older than the last processed tick are ignored.
        Returns:
            Current active candle state for all timeframes, or {} if skipped.
        """
        now = ist_now_naive()

        now_epoch = int(now.replace(tzinfo=IST).timestamp())
        if tick_ts is None:
            tick_ts = now_epoch

        # ── Sanitise broker timestamp ──────────────────────────────────
        if tick_ts < 1.5e9:  # zero, unreasonably old, or fallback — use wall clock
            if ticker not in self._warned_suspicious_ts:
                self._warned_suspicious_ts.add(ticker)
                print(f"[Aggregator] {ticker} broker ts={tick_ts} is suspicious, using server time")
            tick_ts = now_epoch
        elif tick_ts < now_epoch - 30:  # frozen (>30s behind wall clock) — use server time
            if ticker not in self._warned_frozen_ts:
                self._warned_frozen_ts.add(ticker)
                print(f"[Aggregator] {ticker} broker ts={tick_ts} frozen (now_epoch={now_epoch}), using server time")
            tick_ts = now_epoch

        # Use broker timestamp for all bucket calculations (more accurate)
        tick_dt = datetime.fromtimestamp(tick_ts, tz=IST).replace(tzinfo=None)

        with self._lock:
            # ── Tick ordering check (even before market-hours check) ─────
            last_ts = self._last_tick_ts.get(ticker)
            if last_ts is not None and tick_ts < last_ts:
                self._stale_skip_count[ticker] = self._stale_skip_count.get(ticker, 0) + 1
                if self._stale_skip_count[ticker] <= 3:
                    print(f"[Aggregator] Stale tick {ticker}: {tick_ts} < last {last_ts} (skip #{self._stale_skip_count[ticker]})")
                return {}

        # ── Validate market hours & trading day (outside lock) ─────────
        today = tick_dt.date()
        if not is_trading_day(today, self._holidays):
            return {}
        if not is_market_hour(tick_dt):
            # After market close: flush only daily+ candles to DB (intraday is in-memory only)
            if tick_dt.hour >= NSE_CLOSE_HOUR:
                with self._lock:
                    if ticker in self.active_candles and self._last_known_bucket.get(ticker):
                        for tf in ["1m", "5m", "15m", "30m", "1h", "1D", "1W", "1M"]:
                            c = self.active_candles[ticker].get(tf)
                            if c is not None and self._last_known_bucket[ticker].get(tf) is not None:
                                self._flush_candle(ticker, tf, c)
                        del self.active_candles[ticker]
                        self._completed_1m.pop(ticker, None)
                        self._last_known_bucket.pop(ticker, None)
                        self._last_activity.pop(ticker, None)
                self._flush_batch_now()
            return {}

        with self._lock:
            self._last_tick_ts[ticker] = tick_ts
            self._last_activity[ticker] = time.time()

            # ── Periodic cleanup of inactive tickers ────────────────────
            self._gc_inactive()

            # ── Initialize ticker if first tick ──────────────────────────
            if ticker not in self.active_candles:
                self._init_ticker(tick_ts, ticker, price, day_open, day_high, day_low)

            # ── 1m candle: check rollover ────────────────────────────────
            snapped_1m = snap_to_nse_session(tick_ts, 1)
            last_1m_bucket = self._last_known_bucket[ticker]["1m"]
            if snapped_1m != last_1m_bucket:
                prev_1m = self.active_candles[ticker].get("1m")
                if prev_1m is not None:
                    self._on_1m_complete(ticker, prev_1m)
                self.active_candles[ticker]["1m"] = self._create_candle(datetime.fromtimestamp(snapped_1m, tz=IST).replace(tzinfo=None), price)
                self._last_known_bucket[ticker]["1m"] = snapped_1m
                # Diagnostic: log rollover every 5th minute to reduce spam
                minute_bucket = datetime.fromtimestamp(snapped_1m, tz=IST).minute
                if minute_bucket % 5 == 0 or minute_bucket == 8 or minute_bucket == 9:
                    print(f"[Aggregator] {ticker} rollover to {datetime.fromtimestamp(snapped_1m, tz=IST).strftime('%H:%M')} (ts={tick_ts})")

            # ── Update 1m candle with tick ──────────────────────────────
            c1 = self.active_candles[ticker]["1m"]
            assert isinstance(c1["timestamp"], datetime), (
                f"[Aggregator] TYPE VIOLATION: {ticker} 1m timestamp is {type(c1['timestamp']).__name__}")
            c1["high"] = max(c1["high"], price)
            c1["low"] = min(c1["low"], price)
            c1["close"] = price
            c1["volume"] += volume

            # ── Propagate tick to all higher TF forming candles ─────────
            # NOTE: volume is NOT propagated here — it's handled by the
            # aggregation path (when 1m completes) to avoid double-counting.
            for tf in ["5m", "15m", "30m", "1h"]:
                c = self.active_candles[ticker].get(tf)
                if c is not None:
                    assert isinstance(c["timestamp"], datetime), (
                        f"[Aggregator] TYPE VIOLATION: {ticker} {tf} timestamp is {type(c['timestamp']).__name__}")
                    c["high"] = max(c["high"], price)
                    c["low"] = min(c["low"], price)
                    c["close"] = price
            for tf in ["1D", "1W", "1M"]:
                c = self.active_candles[ticker].get(tf)
                if c is not None:
                    assert isinstance(c["timestamp"], datetime), (
                        f"[Aggregator] TYPE VIOLATION: {ticker} {tf} timestamp is {type(c['timestamp']).__name__}")
                    c["high"] = max(c["high"], price)
                    c["low"] = min(c["low"], price)
                    c["close"] = price

            # ── Build response (all TFs including 1D/1W/1M) ─────────────
            res = {}
            for tf, _ in TIMEFRAME_BUCKETS:
                c = self.active_candles[ticker].get(tf)
                if c:
                    o, h, l, c_close = fix_ohlc(c["open"], c["high"], c["low"], c["close"])
                    res[tf] = {
                        "time": self._ts_to_epoch(c["timestamp"]),
                        "open": o, "high": h, "low": l, "close": c_close,
                        "volume": c["volume"],
                    }
            if ticker in self._previous_closes:
                res["previous_close"] = self._previous_closes[ticker]

        # ── Flush completed candles outside the lock ──────────────────
        self._flush_batch_now()
        return res

    def get_current(self, ticker: str) -> Optional[Dict]:
        """Snapshot of current forming candles for all timeframes."""
        with self._lock:
            raw = self.active_candles.get(ticker)
            if not raw:
                return None
            res = {}
            for tf, _ in TIMEFRAME_BUCKETS:
                c = raw.get(tf)
                if c:
                    o, h, l, c_close = fix_ohlc(c["open"], c["high"], c["low"], c["close"])
                    res[tf] = {
                        "time": self._ts_to_epoch(c["timestamp"]),
                        "open": o, "high": h, "low": l, "close": c_close,
                        "volume": c["volume"],
                    }
            return res

    # ── internals ─────────────────────────────────────────────────────

    def _init_ticker(self, now_epoch: int, ticker: str, price: float, day_open: float = None, day_high: float = None, day_low: float = None):
        self.active_candles[ticker] = {}
        self._completed_1m[ticker] = []
        self._last_known_bucket[ticker] = {}

        # Initialize 1m from ticks
        snapped_1m = snap_to_nse_session(now_epoch, 1)
        snapped_1m_dt = datetime.fromtimestamp(snapped_1m, tz=IST).replace(tzinfo=None)
        self.active_candles[ticker]["1m"] = self._create_candle(snapped_1m_dt, price)
        self._last_known_bucket[ticker]["1m"] = snapped_1m

        # Initialize higher TFs
        for tf in ["5m", "15m", "30m", "1h"]:
            bucket_min = AGGREGATION_FACTOR[tf]
            snapped = snap_to_nse_session(now_epoch, bucket_min)
            snapped_dt = datetime.fromtimestamp(snapped, tz=IST).replace(tzinfo=None)
            self.active_candles[ticker][tf] = self._create_candle(snapped_dt, price)
            self._last_known_bucket[ticker][tf] = snapped

        # 1D candle: timestamp = trading date midnight IST
        now_dt = datetime.fromtimestamp(now_epoch, tz=IST)
        today_midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        self.active_candles[ticker]["1D"] = self._create_candle(today_midnight, day_open if day_open is not None else price)
        if day_high is not None: self.active_candles[ticker]["1D"]["high"] = max(price, day_high)
        if day_low is not None: self.active_candles[ticker]["1D"]["low"] = min(price, day_low)
        self._last_known_bucket[ticker]["1D"] = today_midnight.toordinal()  # date ordinal for comparison

        # 1W candle: ISO week start (Monday)
        monday = today_midnight - timedelta(days=today_midnight.weekday())
        self.active_candles[ticker]["1W"] = self._create_candle(monday, price)
        self._last_known_bucket[ticker]["1W"] = monday.toordinal()

        # 1M candle: first of month
        first_of_month = today_midnight.replace(day=1)
        self.active_candles[ticker]["1M"] = self._create_candle(first_of_month, price)
        self._last_known_bucket[ticker]["1M"] = first_of_month.toordinal()

    def _on_1m_complete(self, ticker: str, completed_1m: Dict):
        """A 1m candle just completed. Flush to DB, then aggregate into higher TFs."""
        ts = completed_1m["timestamp"]
        assert isinstance(ts, datetime), (
            f"[Aggregator] TYPE VIOLATION: _on_1m_complete {ticker} timestamp is {type(ts).__name__} val={ts}"
        )
        o, h, l, c = fix_ohlc(
            completed_1m["open"], completed_1m["high"],
            completed_1m["low"], completed_1m["close"]
        )
        flushed = {
            "ticker": ticker,
            "timeframe": "1m",
            "timestamp": ts,
            "open": o, "high": h, "low": l, "close": c,
            "volume": completed_1m["volume"],
            "data_source": "ANGELONE",
            "is_backfilled": False,
        }
        # 1. Enqueue 1m candle for batch DB flush
        self._batch_add(flushed)

        # 2. Capture 1h state BEFORE aggregation (aggregation updates the tracker)
        last_1h_before = self._last_known_bucket[ticker].get("1h")
        prev_1h_before = self.active_candles[ticker].get("1h")
        prev_1h_before = copy.deepcopy(prev_1h_before) if prev_1h_before else None

        # 3. Aggregate into higher TFs via chain: 1m → 5m/15m/30m/1h
        for tf in ["5m", "15m", "30m", "1h"]:
            self._aggregate_intraday(ticker, tf, flushed)

        # 4. When 1h completes, roll into 1D (use pre-loop captured values)
        ts_epoch = self._ts_to_epoch(completed_1m["timestamp"])
        snapped_1h = snap_to_nse_session(ts_epoch, 60)
        if last_1h_before is not None and snapped_1h != last_1h_before:
            if prev_1h_before is not None:
                self._aggregate_1h_into_1d(ticker, prev_1h_before)

    def replay_1m_candle(self, ticker: str, timestamp, open_p, high_p, low_p, close_p, volume: int):
        """Replay a completed 1m candle from yfinance after WS reconnect.
        Feeds it through the aggregation pipeline (higher TFs + DB flush)."""
        from datetime import datetime as _dt
        if isinstance(timestamp, (int, float)):
            timestamp = _dt.fromtimestamp(timestamp, tz=IST).replace(tzinfo=None)
        elif not isinstance(timestamp, _dt):
            raise TypeError(
                f"[Aggregator] replay_1m_candle: {ticker} invalid timestamp type={type(timestamp).__name__} val={timestamp}"
            )
        completed = {
            "timestamp": timestamp,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "volume": volume,
        }
        self._on_1m_complete(ticker, completed)

    def _aggregate_1h_into_1d(self, ticker: str, completed_1h: Dict):
        """Roll a completed 1h candle into the forming 1D candle.
        The 1h is already flushed to DB by _aggregate_intraday — here we only merge into 1D."""
        ts_1h = completed_1h["timestamp"]
        assert isinstance(ts_1h, datetime), (
            f"[Aggregator] TYPE VIOLATION: _aggregate_1h_into_1d {ticker} timestamp is {type(ts_1h).__name__}"
        )
        o, h, l, c = fix_ohlc(
            completed_1h["open"], completed_1h["high"],
            completed_1h["low"], completed_1h["close"]
        )

        # Merge into 1D
        one_d = self.active_candles[ticker].get("1D")
        now_dt = ist_now_naive()
        today_ord = now_dt.toordinal()
        last_1d_ord = self._last_known_bucket[ticker].get("1D", today_ord)

        if today_ord != last_1d_ord:
            # Trading day changed — flush previous 1D and aggregate into 1W/1M
            if one_d is not None:
                self._flush_candle(ticker, "1D", one_d)
                self._aggregate_1d_into_weekly_monthly(ticker, one_d)
            today_midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            self.active_candles[ticker]["1D"] = self._create_candle(today_midnight, o)
            self._last_known_bucket[ticker]["1D"] = today_ord
        elif one_d:
            one_d["high"] = max(one_d["high"], h)
            one_d["low"] = min(one_d["low"], l)
            one_d["close"] = c
            one_d["volume"] += completed_1h["volume"]

    def _aggregate_1d_into_weekly_monthly(self, ticker: str, completed_1d: Dict):
        """When a 1D candle completes, aggregate into 1W and 1M."""
        ts = completed_1d["timestamp"]
        assert isinstance(ts, datetime), (
            f"[Aggregator] TYPE VIOLATION: _aggregate_1d_into_weekly_monthly {ticker} timestamp is {type(ts).__name__}"
        )
        ts_dt = ts

        # ── 1W: ISO week ────────────────────────────────────────────
        monday = (ts_dt - timedelta(days=ts_dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        monday_ord = monday.toordinal()
        last_w = self._last_known_bucket[ticker].get("1W")
        w_candle = self.active_candles[ticker].get("1W")
        if last_w is not None and monday_ord != last_w:
            if w_candle is not None:
                self._flush_candle(ticker, "1W", w_candle)
            self.active_candles[ticker]["1W"] = self._create_candle(monday, completed_1d["open"])
            self._last_known_bucket[ticker]["1W"] = monday_ord
        elif w_candle:
            w_candle["high"] = max(w_candle["high"], completed_1d["high"])
            w_candle["low"] = min(w_candle["low"], completed_1d["low"])
            w_candle["close"] = completed_1d["close"]
            w_candle["volume"] += completed_1d["volume"]

        # ── 1M: Calendar month ───────────────────────────────────────
        first_of_month = ts_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_ord = first_of_month.toordinal()
        last_m = self._last_known_bucket[ticker].get("1M")
        m_candle = self.active_candles[ticker].get("1M")
        if last_m is not None and month_ord != last_m:
            if m_candle is not None:
                self._flush_candle(ticker, "1M", m_candle)
            self.active_candles[ticker]["1M"] = self._create_candle(first_of_month, completed_1d["open"])
            self._last_known_bucket[ticker]["1M"] = month_ord
        elif m_candle:
            m_candle["high"] = max(m_candle["high"], completed_1d["high"])
            m_candle["low"] = min(m_candle["low"], completed_1d["low"])
            m_candle["close"] = completed_1d["close"]
            m_candle["volume"] += completed_1d["volume"]

    def _aggregate_intraday(self, ticker: str, target_tf: str, completed_1m: Dict):
        """Roll a completed 1m candle into a higher-TF forming candle."""
        ts_1m = completed_1m["timestamp"]
        assert isinstance(ts_1m, datetime), (
            f"[Aggregator] TYPE VIOLATION: _aggregate_intraday {ticker} {target_tf} completed_1m timestamp is {type(ts_1m).__name__}"
        )
        bucket_min = AGGREGATION_FACTOR[target_tf]
        ts_epoch = self._ts_to_epoch(ts_1m)
        snapped = snap_to_nse_session(ts_epoch, bucket_min)

        candle = self.active_candles[ticker].get(target_tf)
        if candle is not None:
            assert isinstance(candle["timestamp"], datetime), (
                f"[Aggregator] TYPE VIOLATION: _aggregate_intraday {ticker} {target_tf} forming candle timestamp is {type(candle['timestamp']).__name__}"
            )
        last_bucket = self._last_known_bucket[ticker].get(target_tf)

        if last_bucket is not None and snapped > last_bucket:
            # This forming candle is now complete — flush first
            if candle is not None:
                self._flush_candle(ticker, target_tf, candle)
            # Start new forming candle with this 1m candle's data
            snapped_dt = datetime.fromtimestamp(snapped, tz=IST).replace(tzinfo=None)
            self.active_candles[ticker][target_tf] = {
                "timestamp": snapped_dt,
                "open": completed_1m["open"],
                "high": completed_1m["high"],
                "low": completed_1m["low"],
                "close": completed_1m["close"],
                "volume": completed_1m["volume"],
            }
            self._last_known_bucket[ticker][target_tf] = snapped
        elif candle:
            # Same bucket — merge 1m candle into forming candle
            candle["high"] = max(candle["high"], completed_1m["high"])
            candle["low"] = min(candle["low"], completed_1m["low"])
            candle["close"] = completed_1m["close"]
            candle["volume"] += completed_1m["volume"]

    def _batch_add(self, candle_data: Dict):
        """Add a candle to the pending batch flush."""
        o, h, l, c = fix_ohlc(
            candle_data["open"], candle_data["high"],
            candle_data["low"], candle_data["close"]
        )
        valid, err = validate_ohlc(o, h, l, c)
        if not valid:
            print(f"[Aggregator] INVALID candle {candle_data.get('ticker')} {candle_data.get('timeframe')}: {err}")
        candle_data["open"] = o
        candle_data["high"] = h
        candle_data["low"] = l
        candle_data["close"] = c
        self._flush_batch.append(candle_data)

    def _flush_batch_now(self):
        """Enqueue all accumulated candles for background DB flush."""
        if not self._flush_batch:
            return
        batch = self._flush_batch[:]
        self._flush_batch.clear()
        with self._flush_queue_lock:
            self._flush_id_counter += 1
            self._flush_retries[self._flush_id_counter] = 0
            self._flush_queue.append((self._flush_id_counter, batch))

    def _flush_worker(self):
        """Background thread that drains the flush queue and writes to DB."""
        MAX_RETRIES = 3
        while self._flush_worker_running:
            item = None
            with self._flush_queue_lock:
                if self._flush_queue:
                    item = self._flush_queue.pop(0)
            if item and self.batch_flush_callback:
                fid, batch = item
                try:
                    self.batch_flush_callback(batch)
                except Exception as e:
                    with self._flush_queue_lock:
                        retries = self._flush_retries.get(fid, 0) + 1
                        self._flush_retries[fid] = retries
                    if retries >= MAX_RETRIES:
                        print(f"[Aggregator] CRITICAL: Dropping batch after {MAX_RETRIES} retries ({len(batch)} candles LOST): {e}")
                        with self._flush_queue_lock:
                            self._flush_retries.pop(fid, None)
                    else:
                        print(f"[Aggregator] Background flush error ({len(batch)} candles) retry={retries}/{MAX_RETRIES}: {e}")
                        with self._flush_queue_lock:
                            self._flush_queue.append(item)
            else:
                time.sleep(0.01)  # yield to prevent busy-wait

    def _flush_candle(self, ticker: str, timeframe: str, candle: Dict, data_source: str = "ANGELONE", is_backfilled: bool = False):
        """Add a completed candle to the pending batch flush."""
        o, h, l, c = fix_ohlc(candle["open"], candle["high"], candle["low"], candle["close"])
        flushed = {
            "ticker": ticker,
            "timeframe": timeframe,
            "timestamp": candle["timestamp"],
            "open": o, "high": h, "low": l, "close": c,
            "volume": candle["volume"],
            "is_completed": True,
            "data_source": data_source,
            "is_backfilled": is_backfilled,
        }
        self._batch_add(flushed)

    def _gc_inactive(self):
        """Remove inactive tickers from memory to prevent unbounded growth.
        A ticker is inactive if no tick received for `_ticker_ttl` seconds.
        Rate-limited to once per 60s to avoid O(n) scan on every tick.
        """
        now_ts = time.time()
        if now_ts - self._last_gc < 60:
            return
        self._last_gc = now_ts
        stale = [t for t, last in self._last_activity.items()
                 if now_ts - last > self._ticker_ttl]
        for t in stale:
            self.active_candles.pop(t, None)
            self._completed_1m.pop(t, None)
            self._previous_closes.pop(t, None)
            self._last_tick_ts.pop(t, None)
            self._stale_skip_count.pop(t, None)
            self._last_known_bucket.pop(t, None)
            self._last_activity.pop(t, None)
        if stale:
            print(f"[Aggregator] GC removed {len(stale)} inactive tickers")

    def flush_all_forming(self):
        """Force-complete and flush all forming candles to DB synchronously.
        Collects all flushable candles directly (not through the queue) to avoid
        race conditions with the background worker. Called on server shutdown."""
        print(f"[Aggregator] Flush all forming: stopping worker (queue has {len(self._flush_queue)} batches)")
        self._flush_worker_running = False
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=30)

        all_candles = []
        force_completed = 0

        # 1. Force-complete all forming candles across all TFs (collect directly, bypass queue)
        with self._lock:
            for ticker in list(self.active_candles.keys()):
                for tf in ["1m", "5m", "15m", "30m", "1h", "1D", "1W", "1M"]:
                    c = self.active_candles[ticker].get(tf)
                    if c is not None and self._last_known_bucket.get(ticker, {}).get(tf) is not None:
                        o, h, l, cv = fix_ohlc(
                            c["open"], c["high"], c["low"], c["close"]
                        )
                        flushed = {
                            "ticker": ticker, "timeframe": tf,
                            "timestamp": c["timestamp"],
                            "open": o, "high": h, "low": l, "close": cv,
                            "volume": c["volume"],
                            "is_completed": False,
                        }
                        all_candles.append(flushed)
                        force_completed += 1
                        self.active_candles[ticker].pop(tf, None)
            # 2. Collect any candle data still in the flush buffer
            all_candles.extend(self._flush_batch)
            self._flush_batch.clear()

        # 3. Drain the flush queue (whatever the worker didn't consume)
        queue_drained = 0
        with self._flush_queue_lock:
            for item in self._flush_queue:
                _, batch = item
                all_candles.extend(batch)
                queue_drained += len(batch)
            self._flush_queue.clear()
            self._flush_retries.clear()

        print(f"[Aggregator] Flush: {force_completed} force-completed + {queue_drained} from queue = {len(all_candles)} total")

        # 4. Flush everything in a single synchronous call with retry
        if all_candles and self.batch_flush_callback:
            retries = 3
            while retries > 0:
                try:
                    self.batch_flush_callback(all_candles)
                    print(f"[Aggregator] Shutdown flush OK: {len(all_candles)} candles")
                    break
                except Exception as e:
                    retries -= 1
                    if retries == 0:
                        print(f"[Aggregator] Shutdown flush FAILED after 3 retries ({len(all_candles)} candles LOST): {e}")
                    else:
                        print(f"[Aggregator] Shutdown flush retry ({retries} left): {e}")
                        time.sleep(0.5)

    def get_stats(self) -> dict:
        """Diagnostic stats."""
        with self._lock:
            last_buckets = {}
            for t, kb in self._last_known_bucket.items():
                b = {}
                for tf, v in kb.items():
                    if isinstance(v, int):
                        if v > 100000:  # ordinal or epoch
                            b[tf] = datetime.fromordinal(v).strftime("%m/%d") if v < 800000 else str(v)
                        else:
                            b[tf] = str(v)
                    else:
                        b[tf] = str(v)
                last_buckets[t] = b
            return {
                "tickers": len(self.active_candles),
                "stale_skips": dict(self._stale_skip_count),
                "pending_flush": len(self._flush_batch),
                "queue_size": len(self._flush_queue),
                "last_buckets": last_buckets,
                "worker_alive": self._flush_thread.is_alive() if hasattr(self, '_flush_thread') else False,
            }

    def print_health(self):
        """Print aggregator health to console (called from background task)."""
        s = self.get_stats()
        worker = "ALIVE" if s.get("worker_alive") else "DEAD"
        last = ""
        for t, b in s.get("last_buckets", {}).items():
            last += f" {t}={b.get('1m','?')}"
        total_c = sum(s["stale_skips"].values()) if s["stale_skips"] else 0
        print(f"[Aggregator] Health: {s['tickers']} tickers | worker={worker} | queue={s['queue_size']} | flush_buf={s['pending_flush']} | stale_skip={total_c}{last}")

    def _create_candle(self, timestamp, price: float) -> Dict:
        if isinstance(timestamp, (int, float)):
            ts_dt = datetime.fromtimestamp(int(timestamp), tz=IST).replace(tzinfo=None)
        elif isinstance(timestamp, datetime):
            ts_dt = timestamp
        else:
            print(f"[Aggregator] CRITICAL: _create_candle received unexpected timestamp type={type(timestamp).__name__} value={timestamp}")
            ts_dt = ist_now_naive()
        return {
            "timestamp": ts_dt,
            "open": price, "high": price, "low": price, "close": price,
            "volume": 0,
        }

    @staticmethod
    def _ts_to_epoch(dt) -> int:
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return int(dt.timestamp())
        if isinstance(dt, (int, float)):
            return int(dt)
        return 0


# ── Batch DB flush (transactional) ────────────────────────────────────

def _safe_max_timestamp(candles: List[Dict]) -> str:
    """Compute max timestamp across candles, safely handling mixed types."""
    max_ts = None
    for c in candles:
        ts = c["timestamp"]
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=IST).replace(tzinfo=None)
        elif isinstance(ts, datetime) and ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        if max_ts is None or ts > max_ts:
            max_ts = ts
    return max_ts.strftime("%H:%M") if hasattr(max_ts, "strftime") else str(max_ts)


def batch_flush_candles(candles: List[Dict]):
    """Upsert multiple candles in a single DB transaction using PostgreSQL ON CONFLICT.
    Atomic: all succeed or all roll back. Race-condition safe (no SELECT-then-INSERT gap)."""
    from database import SessionLocal
    from models import Candle
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy import func as sa_func
    if not candles:
        return
    db = None
    try:
        db = SessionLocal()
        for candle in candles:
            ts = candle["timestamp"]
            ts_type = type(ts).__name__
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=IST).replace(tzinfo=None)
                candle["timestamp"] = ts
            assert isinstance(ts, datetime), (
                f"[Aggregator] TYPE MISMATCH: candle[{candle.get('ticker','?')}] "
                f"tf={candle.get('timeframe','?')} timestamp type={ts_type} value={candle.get('timestamp')}"
            )
            insert = pg_insert(Candle).values(
                ticker=candle["ticker"],
                timeframe=candle["timeframe"],
                timestamp=ts,
                open=candle["open"],
                high=candle["high"],
                low=candle["low"],
                close=candle["close"],
                volume=candle["volume"],
                is_completed=candle.get("is_completed", True),
                data_source=candle.get("data_source", "ANGELONE"),
                is_backfilled=candle.get("is_backfilled", False),
            )
            stmt = insert.on_conflict_do_update(
                constraint="uix_candle_key",
                set_={
                    "high": sa_func.greatest(Candle.high, insert.excluded.high),
                    "low": sa_func.least(Candle.low, insert.excluded.low),
                    "open": Candle.open,
                    "close": insert.excluded.close,
                    "volume": insert.excluded.volume,
                    "is_completed": insert.excluded.is_completed,
                },
                where=~((Candle.is_completed == True) & (Candle.volume > insert.excluded.volume) & (Candle.close != insert.excluded.close)),
            )
            db.execute(stmt)
        db.commit()
        counter = getattr(batch_flush_candles, '_flush_counter', 0) + 1
        batch_flush_candles._flush_counter = counter
        # Diagnostic: log flush summary (every 10th batch, or if >5 candles)
        if len(candles) > 5 or counter % 10 == 0:
            tickers = set(c["ticker"] for c in candles)
            tfs = set(c["timeframe"] for c in candles)
            ts_str = _safe_max_timestamp(candles)
            print(f"[Aggregator] Flushed {len(candles)} candles tickers={tickers} tfs={tfs} latest_ts={ts_str}")
    except Exception as e:
        if db:
            try:
                db.rollback()
            except Exception:
                pass
        tickers_info = set((c.get("ticker","?"), c.get("timeframe","?"), type(c.get("timestamp")).__name__) for c in candles)
        print(f"[Aggregator] Batch DB flush error ({len(candles)} candles) tickers_info={tickers_info}: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


# ── Candle health & alerts ───────────────────────────────────────────

HEALTH_ALERTS = {
    "empty_empty_count": {"warn": 10, "crit": 50},
    "stale_skip_rate": {"warn": 0.05, "crit": 0.20},
    "ohlc_invalid_percent": {"warn": 1.0, "crit": 5.0},
}

def compute_candle_health(ticker: Optional[str] = None, db_session=None) -> dict:
    """Aggregated data quality + alert status."""
    from models import Candle
    from sqlalchemy import func as sa_func

    if db_session is None:
        return {"error": "no db session"}

    total = db_session.query(Candle).count()
    by_tf = {}
    for row in db_session.query(Candle.timeframe, sa_func.count(Candle.id)).group_by(Candle.timeframe).all():
        by_tf[row[0]] = row[1]

    recent_24h = db_session.query(Candle).filter(
        Candle.timestamp >= (ist_now_naive() - timedelta(hours=24))
    ).count()

    invalid = db_session.query(Candle).filter(
        ~((Candle.high >= Candle.open) & (Candle.high >= Candle.close) &
          (Candle.low <= Candle.open) & (Candle.low <= Candle.close) &
          (Candle.high >= Candle.low))
    ).count()

    stats = candle_aggregator.get_stats() if hasattr(candle_aggregator, 'get_stats') else {}

    alerts = []
    empty_count = db_session.query(Candle).filter(Candle.volume == 0).count()
    if empty_count > HEALTH_ALERTS["empty_empty_count"]["crit"]:
        alerts.append({"level": "critical", "check": "empty_candles", "value": empty_count})
    elif empty_count > HEALTH_ALERTS["empty_empty_count"]["warn"]:
        alerts.append({"level": "warning", "check": "empty_candles", "value": empty_count})

    ohlc_invalid_pct = (invalid / total * 100) if total else 0
    if ohlc_invalid_pct > HEALTH_ALERTS["ohlc_invalid_percent"]["crit"]:
        alerts.append({"level": "critical", "check": "ohlc_invalid", "value": round(ohlc_invalid_pct, 2)})
    elif ohlc_invalid_pct > HEALTH_ALERTS["ohlc_invalid_percent"]["warn"]:
        alerts.append({"level": "warning", "check": "ohlc_invalid", "value": round(ohlc_invalid_pct, 2)})

    stale_total = sum(stats.get("stale_skips", {}).values())
    recent_candles = max(recent_24h, 1)
    stale_rate = stale_total / recent_candles if recent_candles else 0
    if stale_rate > HEALTH_ALERTS["stale_skip_rate"]["crit"]:
        alerts.append({"level": "critical", "check": "stale_skip_rate", "value": round(stale_rate, 4)})
    elif stale_rate > HEALTH_ALERTS["stale_skip_rate"]["warn"]:
        alerts.append({"level": "warning", "check": "stale_skip_rate", "value": round(stale_rate, 4)})

    return {
        "total_candles": total,
        "by_timeframe": by_tf,
        "recent_24h": recent_24h,
        "invalid_ohlc": invalid,
        "empty_volume_count": empty_count,
        "tickers_in_memory": stats.get("tickers", 0),
        "pending_flush": stats.get("pending_flush", 0),
        "stale_skips_total": stale_total,
        "alerts": alerts,
        "alert_count": len(alerts),
    }


# ── Gap detection (used by GET /api/candle-gaps) ──────────────────────
def detect_missing_intervals(ticker: str, timeframe: str, db_session, start_date=None, end_date=None):
    """Return list of missing datetime ranges for a ticker+timeframe."""
    from sqlalchemy import func as sa_func
    q = db_session.query(Candle.timestamp).filter(
        Candle.ticker == ticker,
        Candle.timeframe == timeframe,
        Candle.is_completed == True,
    )
    if start_date:
        q = q.filter(Candle.timestamp >= start_date)
    if end_date:
        q = q.filter(Candle.timestamp <= end_date)
    q = q.order_by(Candle.timestamp.asc())
    rows = q.all()
    existing = {r[0] for r in rows}
    gaps = []
    if start_date and end_date:
        cur = start_date
        while cur <= end_date:
            if cur not in existing:
                gaps.append(cur.strftime("%Y-%m-%d %H:%M"))
            cur += timedelta(minutes={"1m":1,"5m":5,"15m":15,"30m":30,"1h":60}.get(timeframe, 5))
    return gaps


# ── Global singleton ──────────────────────────────────────────────────
candle_aggregator = LiveCandleAggregator()
candle_aggregator.batch_flush_callback = batch_flush_candles
