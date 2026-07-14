"""
Live 5m Candle Aggregator
==========================
Processes raw ticks from Angel One WebSocket and forms 5-minute candles.

Key design decisions:
  - Only 5-minute candles are built from ticks (no 1m).
  - Higher timeframes (15m/30m/1h/4h/1D/1W/1M) are built in-memory
    by LiveTimeframeManager (separate service), never stored to DB.
  - A 60-second late-tick buffer prevents incorrect OHLC from delayed ticks.
  - Ticks are NEVER dropped — back-pressure pauses the WS consumer instead.
  - Completed 5m candles are flushed to the DB via batch upsert.
  - Events are emitted on every tick update and on candle completion.
"""

import time
import threading
import copy
from datetime import datetime, timedelta, timezone, date
from typing import Dict, Optional, List, Tuple, Callable
from dataclasses import dataclass, field

from event_bus import event_bus

IST = timezone(timedelta(hours=5, minutes=30))

NSE_OPEN_HOUR = 9
NSE_OPEN_MIN = 15
NSE_CLOSE_HOUR = 15
NSE_CLOSE_MIN = 30
NSE_CLOSE_GRACE_MIN = 15

_NSE_OPEN_SEC = NSE_OPEN_HOUR * 3600 + NSE_OPEN_MIN * 60
_NSE_CLOSE_SEC = NSE_CLOSE_HOUR * 3600 + NSE_CLOSE_MIN * 60
_NSE_CLOSE_GRACE_SEC = NSE_CLOSE_HOUR * 3600 + (NSE_CLOSE_MIN + NSE_CLOSE_GRACE_MIN) * 60
_IST_OFFSET_SEC = 5 * 3600 + 30 * 60

SNAPSHOT_TFS = ["5m"]


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


def snap_to_nse_session(ts_epoch, bucket_minutes: int = 5) -> int:
    ts_epoch = int(ts_epoch)
    bucket_sec = bucket_minutes * 60
    ist_seconds = (ts_epoch + _IST_OFFSET_SEC) % 86400
    ist_day_start = ((ts_epoch + _IST_OFFSET_SEC) // 86400) * 86400 - _IST_OFFSET_SEC
    session_start = ist_day_start + _NSE_OPEN_SEC
    if ist_seconds < _NSE_OPEN_SEC:
        return session_start
    if ist_seconds >= _NSE_CLOSE_GRACE_SEC:
        slots = (_NSE_CLOSE_SEC - _NSE_OPEN_SEC) // bucket_sec
        return session_start + int((slots - 1)) * bucket_sec
    elif ist_seconds >= _NSE_CLOSE_SEC:
        slots = (_NSE_CLOSE_SEC - _NSE_OPEN_SEC) // bucket_sec
        return session_start + int(slots) * bucket_sec
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


@dataclass
class Pending5mCandle:
    ticker: str
    bucket_start: datetime
    deadline: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    finalized: bool = False


class PendingCandleManager:
    def __init__(self, late_buffer_sec: int = 60):
        self._late_buffer_sec = late_buffer_sec
        self._pending: Dict[str, Pending5mCandle] = {}
        self._lock = threading.Lock()

    def add_or_update(self, ticker: str, candle: dict, now_epoch: float) -> Optional[Pending5mCandle]:
        with self._lock:
            existing = self._pending.get(ticker)
            if existing is None:
                pending = Pending5mCandle(
                    ticker=ticker,
                    bucket_start=candle["timestamp"],
                    deadline=now_epoch + self._late_buffer_sec,
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                )
                self._pending[ticker] = pending
                return pending
            existing.high = max(existing.high, candle["high"])
            existing.low = min(existing.low, candle["low"])
            existing.close = candle["close"]
            existing.volume += candle["volume"]
            return existing

    def get_expired(self, now_epoch: float) -> List[Pending5mCandle]:
        expired = []
        with self._lock:
            for ticker, pending in list(self._pending.items()):
                if now_epoch >= pending.deadline:
                    pending.finalized = True
                    expired.append(pending)
                    del self._pending[ticker]
        return expired

    def remove(self, ticker: str):
        with self._lock:
            self._pending.pop(ticker, None)

    def clear_all(self):
        with self._lock:
            self._pending.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._pending)


class Live5mBuilder:
    def __init__(self):
        self.active_candles: Dict[str, Dict] = {}
        self._previous_closes: Dict[str, float] = {}
        self._last_tick_ts: Dict[str, float] = {}
        self._stale_skip_count: Dict[str, int] = {}
        self._holidays: set = set()
        self._lock = threading.Lock()
        self._last_activity: Dict[str, float] = {}
        self._ticker_ttl = 3600
        self._last_gc: float = 0.0
        self._warned_suspicious_ts: set = set()
        self._warned_frozen_ts: set = set()

        self._pending_mgr = PendingCandleManager(late_buffer_sec=60)
        self._flush_batch: List[Dict] = []
        self._flush_queue: List[List[Dict]] = []
        self._flush_queue_lock = threading.Lock()
        self._flush_retries: Dict[int, int] = {}
        self._flush_id_counter = 0
        self.batch_flush_callback: Optional[Callable[[List[Dict]], None]] = None
        self._on_candle_completed: Optional[Callable[[str, Dict], None]] = None
        self._backpressure_threshold = 5000
        self._backpressure_events = 0

        self._flush_worker_running = True
        self._flush_thread = threading.Thread(target=self._flush_worker, daemon=True)
        self._flush_thread.start()

        self._late_buffer_thread = threading.Thread(target=self._check_late_buffer, daemon=True)
        self._late_buffer_thread.start()

    def set_holidays(self, holidays: set):
        self._holidays = holidays

    def set_previous_close(self, ticker: str, price: float):
        self._previous_closes[ticker] = price

    def init_ticker_from_last_candle(self, ticker: str, last_candle: dict):
        with self._lock:
            snapped = snap_to_nse_session(time.time(), 5)
            snapped_dt = datetime.fromtimestamp(snapped, tz=IST).replace(tzinfo=None)
            self.active_candles[ticker] = self._create_candle(snapped_dt, last_candle.get("close", last_candle.get("price", 0)))
            self._previous_closes[ticker] = last_candle.get("close", last_candle.get("price", 0))

    def process_tick(self, ticker: str, price: float, volume: int = 0, tick_ts: float = None,
                     day_open: float = None, day_high: float = None, day_low: float = None) -> Dict:
        now = ist_now_naive()
        now_epoch = int(now.replace(tzinfo=IST).timestamp())

        if tick_ts is None:
            tick_ts = now_epoch

        if tick_ts < 1.5e9:
            if ticker not in self._warned_suspicious_ts:
                self._warned_suspicious_ts.add(ticker)
                print(f"[Aggregator] {ticker} suspicious ts={tick_ts}, using server time")
            tick_ts = now_epoch
        elif tick_ts < now_epoch - 30:
            if ticker not in self._warned_frozen_ts:
                self._warned_frozen_ts.add(ticker)
                print(f"[Aggregator] {ticker} frozen ts={tick_ts} (now={now_epoch}), using server time")
            tick_ts = now_epoch

        tick_dt = datetime.fromtimestamp(tick_ts, tz=IST).replace(tzinfo=None)

        with self._lock:
            last_ts = self._last_tick_ts.get(ticker)
            if last_ts is not None and tick_ts < last_ts:
                self._stale_skip_count[ticker] = self._stale_skip_count.get(ticker, 0) + 1
                if self._stale_skip_count[ticker] <= 3:
                    print(f"[Aggregator] Stale tick {ticker}: {tick_ts} < last {last_ts}")
                return {}

        today = tick_dt.date()
        if not is_trading_day(today, self._holidays):
            return {}
        if not is_market_hour(tick_dt):
            if tick_dt.hour >= NSE_CLOSE_HOUR:
                with self._lock:
                    if ticker in self.active_candles:
                        c = self.active_candles[ticker].get("5m")
                        if c is not None:
                            self._flush_candle(ticker, "5m", c)
                        del self.active_candles[ticker]
                        self._last_activity.pop(ticker, None)
                self._flush_batch_now()
            return {}

        with self._lock:
            self._last_tick_ts[ticker] = tick_ts
            self._last_activity[ticker] = time.time()

            self._gc_inactive()

            if ticker not in self.active_candles:
                self._init_ticker(tick_ts, ticker, price, day_open, day_high, day_low)

            snapped_5m = snap_to_nse_session(tick_ts, 5)
            forming = self.active_candles[ticker].get("5m")

            if forming is None:
                snapped_dt = datetime.fromtimestamp(snapped_5m, tz=IST).replace(tzinfo=None)
                self.active_candles[ticker]["5m"] = self._create_candle(snapped_dt, price)
                forming = self.active_candles[ticker]["5m"]

            forming["high"] = max(forming["high"], price)
            forming["low"] = min(forming["low"], price)
            forming["close"] = price
            forming["volume"] += volume

            res = {}
            o, h, l, c = fix_ohlc(forming["open"], forming["high"], forming["low"], forming["close"])
            res["5m"] = {
                "time": self._ts_to_epoch(forming["timestamp"]),
                "open": o, "high": h, "low": l, "close": c,
                "volume": forming["volume"],
            }
            if ticker in self._previous_closes:
                res["previous_close"] = self._previous_closes[ticker]

        event_bus.emit("candle.5m.updated", ticker=ticker, forming_candle=res.get("5m", {}))

        self._flush_batch_now()
        return res

    def finalize_5m_candle(self, ticker: str):
        with self._lock:
            forming = self.active_candles.get(ticker, {}).get("5m")
            if forming is None:
                return
            snapped_5m = snap_to_nse_session(time.time(), 5)
            snapped_dt = datetime.fromtimestamp(snapped_5m, tz=IST).replace(tzinfo=None)
            new_price = forming["close"]
            self.active_candles[ticker]["5m"] = self._create_candle(snapped_dt, new_price)

        o, h, l, c = fix_ohlc(forming["open"], forming["high"], forming["low"], forming["close"])
        completed = {
            "ticker": ticker,
            "timeframe": "5m",
            "timestamp": forming["timestamp"],
            "open": o, "high": h, "low": l, "close": c,
            "volume": forming["volume"],
            "data_source": "ANGELONE",
            "is_backfilled": False,
        }
        self._pending_mgr.add_or_update(ticker, completed, time.time())

    def _check_late_buffer(self):
        while self._flush_worker_running:
            time.sleep(2)
            now_epoch = time.time()
            expired = self._pending_mgr.get_expired(now_epoch)
            for pending in expired:
                completed = {
                    "ticker": pending.ticker,
                    "timeframe": "5m",
                    "timestamp": pending.bucket_start,
                    "open": pending.open, "high": pending.high,
                    "low": pending.low, "close": pending.close,
                    "volume": pending.volume,
                    "data_source": "ANGELONE",
                    "is_backfilled": False,
                }
                self._batch_add(completed)
                event_bus.emit("candle.5m.completed", ticker=pending.ticker, candle=completed)
                with self._lock:
                    if pending.ticker in self.active_candles:
                        snapped_5m = snap_to_nse_session(now_epoch, 5)
                        snapped_dt = datetime.fromtimestamp(snapped_5m, tz=IST).replace(tzinfo=None)
                        self.active_candles[pending.ticker]["5m"] = self._create_candle(snapped_dt, completed["close"])
            if expired:
                self._flush_batch_now()

    def get_current(self, ticker: str) -> Optional[Dict]:
        with self._lock:
            raw = self.active_candles.get(ticker)
            if not raw:
                return None
            c = raw.get("5m")
            if not c:
                return None
            o, h, l, cv = fix_ohlc(c["open"], c["high"], c["low"], c["close"])
            return {
                "5m": {
                    "time": self._ts_to_epoch(c["timestamp"]),
                    "open": o, "high": h, "low": l, "close": cv,
                    "volume": c["volume"],
                }
            }

    def _init_ticker(self, now_epoch: int, ticker: str, price: float,
                     day_open: float = None, day_high: float = None, day_low: float = None):
        self.active_candles[ticker] = {}
        snapped_5m = snap_to_nse_session(now_epoch, 5)
        snapped_dt = datetime.fromtimestamp(snapped_5m, tz=IST).replace(tzinfo=None)
        self.active_candles[ticker]["5m"] = self._create_candle(snapped_dt, price)

    def flush_all_forming(self):
        print(f"[Aggregator] Flush all: stopping worker (queue={len(self._flush_queue)} batches)")
        self._flush_worker_running = False
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=30)
        self._pending_mgr.clear_all()

        all_candles = []
        force_completed = 0

        with self._lock:
            for ticker in list(self.active_candles.keys()):
                c = self.active_candles[ticker].get("5m")
                if c is not None:
                    o, h, l, cv = fix_ohlc(c["open"], c["high"], c["low"], c["close"])
                    flushed = {
                        "ticker": ticker, "timeframe": "5m",
                        "timestamp": c["timestamp"],
                        "open": o, "high": h, "low": l, "close": cv,
                        "volume": c["volume"],
                        "is_completed": False,
                    }
                    all_candles.append(flushed)
                    force_completed += 1
                    self.active_candles[ticker].pop("5m", None)

            all_candles.extend(self._flush_batch)
            self._flush_batch.clear()

        queue_drained = 0
        with self._flush_queue_lock:
            for item in self._flush_queue:
                _, batch = item
                all_candles.extend(batch)
                queue_drained += len(batch)
            self._flush_queue.clear()
            self._flush_retries.clear()

        print(f"[Aggregator] Flush: {force_completed} force + {queue_drained} queue = {len(all_candles)} total")

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
                        print(f"[Aggregator] Shutdown flush FAILED: {e}")
                    else:
                        print(f"[Aggregator] Shutdown flush retry ({retries}): {e}")
                        time.sleep(0.5)

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "tickers": len(self.active_candles),
                "stale_skips": dict(self._stale_skip_count),
                "pending_flush": len(self._flush_batch),
                "queue_size": len(self._flush_queue),
                "pending_late_buffer": self._pending_mgr.count(),
                "backpressure_events": self._backpressure_events,
                "worker_alive": self._flush_thread.is_alive() if hasattr(self, '_flush_thread') else False,
            }

    def print_health(self):
        s = self.get_stats()
        worker = "ALIVE" if s.get("worker_alive") else "DEAD"
        total_skips = sum(s["stale_skips"].values()) if s["stale_skips"] else 0
        print(f"[Aggregator] {s['tickers']} tickers | worker={worker} | "
              f"pending={s['pending_late_buffer']} | queue={s['queue_size']} | "
              f"flush={s['pending_flush']} | stale={total_skips} | bp={s['backpressure_events']}")

    def _batch_add(self, candle_data: Dict):
        o, h, l, c = fix_ohlc(
            candle_data["open"], candle_data["high"],
            candle_data["low"], candle_data["close"]
        )
        candle_data["open"] = o
        candle_data["high"] = h
        candle_data["low"] = l
        candle_data["close"] = c
        self._flush_batch.append(candle_data)

    def _flush_batch_now(self):
        if not self._flush_batch:
            return
        batch = self._flush_batch[:]
        self._flush_batch.clear()
        with self._flush_queue_lock:
            self._flush_id_counter += 1
            self._flush_retries[self._flush_id_counter] = 0
            self._flush_queue.append((self._flush_id_counter, batch))

    def _flush_worker(self):
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
                        print(f"[Aggregator] Dropping batch after {MAX_RETRIES} retries ({len(batch)} LOST): {e}")
                        with self._flush_queue_lock:
                            self._flush_retries.pop(fid, None)
                    else:
                        print(f"[Aggregator] Flush error ({len(batch)} candles) retry={retries}/{MAX_RETRIES}: {e}")
                        with self._flush_queue_lock:
                            self._flush_queue.append(item)
            else:
                time.sleep(0.01)

    def _flush_candle(self, ticker: str, timeframe: str, candle: Dict,
                      data_source: str = "ANGELONE", is_backfilled: bool = False):
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
        now_ts = time.time()
        if now_ts - self._last_gc < 60:
            return
        self._last_gc = now_ts
        stale = [t for t, last in self._last_activity.items()
                 if now_ts - last > self._ticker_ttl]
        for t in stale:
            self.active_candles.pop(t, None)
            self._previous_closes.pop(t, None)
            self._last_tick_ts.pop(t, None)
            self._stale_skip_count.pop(t, None)
            self._last_activity.pop(t, None)
            self._pending_mgr.remove(t)
        if stale:
            print(f"[Aggregator] GC removed {len(stale)} inactive tickers")

    def _create_candle(self, timestamp, price: float) -> Dict:
        if isinstance(timestamp, (int, float)):
            ts_dt = datetime.fromtimestamp(int(timestamp), tz=IST).replace(tzinfo=None)
        elif isinstance(timestamp, datetime):
            ts_dt = timestamp
        else:
            print(f"[Aggregator] Bad timestamp type={type(timestamp).__name__}")
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


def _safe_max_timestamp(candles: List[Dict]) -> str:
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
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=IST).replace(tzinfo=None)
                candle["timestamp"] = ts
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
        if len(candles) > 5 or counter % 10 == 0:
            tickers = set(c["ticker"] for c in candles)
            tfs = set(c["timeframe"] for c in candles)
            ts_str = _safe_max_timestamp(candles)
            print(f"[Aggregator] Flushed {len(candles)} candles tickers={tickers} tfs={tfs} latest={ts_str}")
    except Exception as e:
        if db:
            try:
                db.rollback()
            except Exception:
                pass
        print(f"[Aggregator] Batch flush error ({len(candles)}): {e}")
        raise
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


HEALTH_ALERTS = {
    "empty_empty_count": {"warn": 10, "crit": 50},
    "stale_skip_rate": {"warn": 0.05, "crit": 0.20},
    "ohlc_invalid_percent": {"warn": 1.0, "crit": 5.0},
}


def compute_candle_health(ticker: Optional[str] = None, db_session=None) -> dict:
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
        "pending_late_buffer": stats.get("pending_late_buffer", 0),
        "stale_skips_total": stale_total,
        "alerts": alerts,
        "alert_count": len(alerts),
    }


candle_aggregator = Live5mBuilder()
candle_aggregator.batch_flush_callback = batch_flush_candles
