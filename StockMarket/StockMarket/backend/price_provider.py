from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

import database
import models


class PriceSource(str, Enum):
    LIVE_TICK = "live_tick"
    FORMING_5M = "forming_5m"
    COMPLETED_5M = "completed_5m"
    DAILY_CLOSE = "daily_close"
    NONE = "none"


@dataclass(frozen=True)
class PriceSnapshot:
    """
    Immutable result of resolving a ticker's current price.

    Read-only after construction: all attributes are final, the dataclass is
    frozen, and no consumer may populate shared runtime state from it.
    """

    ticker: str
    price: float
    source: PriceSource = field(default=PriceSource.NONE)
    timestamp: Optional[datetime] = field(default=None)


class PriceProvider:
    """
    Resolves the current price for a ticker using a four-tier fallback pipeline:

      1. Live tick cache   (angelone_service.latest_ticks)      - sub-second
      2. Forming 5m candle (candle_aggregator.get_current)      - in-memory
      3. Completed 5m row  (candles table)                      - latest IST 5m
      4. Daily close       (stock_data table)                   - previous day

    Tiers 1-3 supply near-live values. Tier 4 (yesterday's close) is only
    consulted outside market hours as a last resort, never during the session,
    so execution never acts on a stale daily value while the market is open.

    The provider is strictly read-only over shared runtime state. It acquires
    the live-tick lock only to snapshot reads, never mutates any aggregator or
    broker structure, and draws no background threads, timers, or workers.
    """

    def __init__(
        self,
        angelone_service=None,
        candle_aggregator=None,
        session_factory: Optional[Callable[[], Session]] = None,
        market_open: Optional[Callable[[datetime], bool]] = None,
        max_tick_age: float = 30.0,
        max_derived_age: float = 420.0,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self._angel = angelone_service
        self._aggregator = candle_aggregator
        self._session_factory = session_factory or database.SessionLocal
        self._market_open = market_open
        self._max_tick_age = max_tick_age
        # Tiers 2-3 (forming/completed 5m) had no staleness check at all --
        # unlike tier 1's 30s tick-age check, a candle from an hour ago could
        # be returned as "current price" if the live feed died. 420s = the
        # 5-minute bucket width a fresh forming candle can legitimately still
        # be within, plus the same 120s stale-tick grace period the rest of
        # this codebase already uses to decide a ticker's feed has gone dead
        # (main.py's _ws_watchdog). Composed from existing constants, not a
        # new arbitrary number.
        self._max_derived_age = max_derived_age
        self._now_fn = now_fn or (lambda: datetime.now())

    def get_prices(self, tickers: List[str]) -> Dict[str, PriceSnapshot]:
        """Resolve prices for a list of tickers, batching DB tiers so that at
        most a constant number of queries is issued regardless of list size.

        Reviews are resolved by priority; once every ticker has a snapshot (or
        the pipeline is exhausted), remaining DB work is skipped.
        """
        result: Dict[str, PriceSnapshot] = {}
        pending = [t for t in dict.fromkeys(tickers) if t]

        # Tier 1: live tick cache (in-memory, single lock snapshot)
        pending = self._resolve_tier_live(result, pending)
        if not pending:
            return result

        # Tier 2: forming 5m (in-memory, per-ticker aggregator reads)
        pending = self._resolve_tier_forming(result, pending)
        if not pending:
            return result

        # Tier 3: completed 5m (single batched DB query)
        pending = self._resolve_tier_completed(result, pending)
        if not pending:
            return result

        # Tier 4: daily close, only outside market hours (single batched query)
        if not self._is_market_open():
            pending = self._resolve_tier_daily(result, pending)

        return result

    def get_price(self, ticker: str) -> Optional[PriceSnapshot]:
        """Resolve a single ticker, following the same four-tier pipeline."""
        rows = self.get_prices([ticker])
        return rows.get(ticker)

    def _resolve_tier_live(self, result, pending):
        if self._angel is None or not pending:
            return pending
        try:
            with self._angel.latest_ticks_lock:
                snapshot = dict(self._angel.latest_ticks)
        except Exception:
            return pending
        now_ts = self._now_fn().timestamp()
        still = []
        for ticker in pending:
            entry = snapshot.get(ticker)
            if not entry:
                still.append(ticker)
                continue
            price = entry.get("current_price")
            if price is None:
                still.append(ticker)
                continue
            received = entry.get("_received_ts") or entry.get("_ts") or 0
            try:
                age = now_ts - float(received)
            except Exception:
                age = 0.0
            if age > self._max_tick_age:
                still.append(ticker)
                continue
            value = float(price)
            if value > 0:
                result[ticker] = PriceSnapshot(
                    ticker=ticker, price=value, source=PriceSource.LIVE_TICK
                )
            else:
                still.append(ticker)
        return still

    def _resolve_tier_forming(self, result, pending):
        if self._aggregator is None or not pending:
            return pending
        still = []
        for ticker in pending:
            try:
                current = self._aggregator.get_current(ticker)
            except Exception:
                current = None
            if not current:
                still.append(ticker)
                continue
            candle = current.get("5m")
            if not candle:
                still.append(ticker)
                continue
            # Staleness check: a forming candle's bucket only advances when a
            # tick arrives. If the feed died, it can sit at the same bucket
            # indefinitely -- "time" not present (e.g. some callers/tests
            # don't model it) is treated as unknown and passed through
            # rather than rejected, matching this tier's existing
            # permissive-on-missing-data behavior.
            bucket_time = candle.get("time")
            if bucket_time is not None:
                try:
                    age = self._now_fn().timestamp() - float(bucket_time)
                except Exception:
                    age = 0.0
                if age > self._max_derived_age:
                    still.append(ticker)
                    continue
            close = candle.get("close")
            if close is None:
                still.append(ticker)
                continue
            value = float(close)
            if value > 0:
                result[ticker] = PriceSnapshot(
                    ticker=ticker, price=value, source=PriceSource.FORMING_5M
                )
            else:
                still.append(ticker)
        return still

    def _resolve_tier_completed(self, result, pending):
        if not pending:
            return pending
        db = None
        try:
            db = self._session_factory()
            today = database.get_ist_now().date()
            rows = (
                db.query(models.Candle)
                .filter(
                    models.Candle.ticker.in_(pending),
                    models.Candle.timeframe == "5m",
                    models.Candle.is_completed == True,
                    func.date(models.Candle.timestamp) == today,
                )
                .order_by(models.Candle.timestamp.desc())
                .all()
            )
            latest: Dict[str, models.Candle] = {}
            for r in rows:
                if r.ticker not in latest:
                    latest[r.ticker] = r
            now_ist = database.get_ist_now()
            still = []
            for ticker in pending:
                found = latest.get(ticker)
                if found is None or found.close is None:
                    still.append(ticker)
                    continue
                # Staleness check -- see tier 2's comment above for why this
                # exists. Candle.timestamp is stored as naive IST (matches
                # get_ist_now(), not self._now_fn() which may be a different
                # clock/timezone in tests); missing timestamp is treated as
                # unknown and passed through rather than rejected.
                candle_ts = getattr(found, "timestamp", None)
                if candle_ts is not None:
                    try:
                        age = (now_ist - candle_ts).total_seconds()
                    except Exception:
                        age = 0.0
                    if age > self._max_derived_age:
                        still.append(ticker)
                        continue
                value = float(found.close)
                if value > 0:
                    result[ticker] = PriceSnapshot(
                        ticker=ticker, price=value, source=PriceSource.COMPLETED_5M
                    )
                else:
                    still.append(ticker)
            return still
        except Exception:
            db.rollback() if db is not None else None
            return pending
        finally:
            if db is not None:
                db.close()

    def _resolve_tier_daily(self, result, pending):
        if not pending:
            return pending
        db = None
        try:
            db = self._session_factory()
            subq = (
                db.query(
                    models.Candle.ticker,
                    func.max(models.Candle.timestamp).label("max_ts"),
                )
                .filter(
                    models.Candle.ticker.in_(pending),
                    models.Candle.timeframe == "1D",
                )
                .group_by(models.Candle.ticker)
                .subquery()
            )
            rows = (
                db.query(models.Candle)
                .join(
                    subq,
                    (models.Candle.ticker == subq.c.ticker)
                    & (models.Candle.timestamp == subq.c.max_ts)
                    & (models.Candle.timeframe == "1D"),
                )
                .all()
            )
            latest: Dict[str, models.Candle] = {}
            for r in rows:
                latest[r.ticker] = r
            still = []
            for ticker in pending:
                found = latest.get(ticker)
                if found is None or found.close is None:
                    still.append(ticker)
                    continue
                value = float(found.close)
                if value > 0:
                    result[ticker] = PriceSnapshot(
                        ticker=ticker, price=value, source=PriceSource.DAILY_CLOSE
                    )
                else:
                    still.append(ticker)
            return still
        except Exception as exc:
            db.rollback() if db is not None else None
            return pending
        finally:
            if db is not None:
                db.close()

    def _is_market_open(self) -> bool:
        if self._market_open is not None:
            try:
                return bool(self._market_open(self._now_fn()))
            except Exception:
                return False
        try:
            from exchange_calendar import nse_calendar

            return nse_calendar.is_market_open(self._now_fn())
        except Exception:
            return False


price_provider = PriceProvider()