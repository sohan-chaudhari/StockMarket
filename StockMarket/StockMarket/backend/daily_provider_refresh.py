"""Authoritative 1-D (daily) candles from AngelOne ``ONE_DAY``.

Measured 2026-10-28 against the exchange quote (post-close, so the quote *is* the
session truth) versus the 1D row this platform served:

    ticker     exchange O / H / L / C                our 1D row
    CUPID      317.00 / 317.90 / 309.00 / 314.55     317.20 / 317.90 / 309.00 / 316.00
    TCS        2449.00 / 2461.30 / 2408.20 / 2460    2447.10 / 2479.00 / 2408.20 / 2460
    RELIANCE   1194.50 / 1204.30 / 1156.40 / 1158    exact match

Our 5m series stops at the 15:25 bar, while the official close is produced by the
15:30 call auction. A daily aggregated from 5m therefore can never carry the real
close (CUPID 316.00 in the DB against 314.55 on the exchange), and any bad print
inside a 5m bar is inherited the same way (TCS's high is 17.70 too high).

``ONE_DAY`` carries the exchange's own open/high/low/close/volume, so it is the
authority for the daily and is written with ``data_source='ANGELONE'``.

Rules
-----
* **Never fabricate.** A candle is written only when the provider returned a
  complete, internally consistent one: ``open > 0``, ``close > 0``,
  ``high >= max(open, close)`` and ``low <= min(open, close)``. Anything else is
  skipped and retried on a later run.
* **Bounded and convergent.** At most ``limit`` tickers and ``budget_seconds`` of
  wall clock per call, and only rows that are *missing* or still carry a derived
  source (``5M_AGG`` / ``BACKFILL`` / ``YFINANCE``). Repeated calls finish the
  universe and then become a cheap no-op; rows already written from the exchange
  are never re-fetched.
* **Paced.** All provider access goes through ``historical_service``, so the
  shared rate limiter and cooldown apply. This must never starve the 5m backfill
  queue that is filling the universe's charts: ``priority`` defaults to False.
* **Idempotent.** UPSERT on ``(ticker, timeframe, timestamp)`` -- running it twice
  writes identical values.
* **Non-fatal.** Every failure is logged and the ticker is retried next run; this
  function never raises into a caller.

Scheduling lives in ``main.py`` (``_daily_bar_refresh_loop``), which calls this
only after the close.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

# Rows carrying these sources were derived (5m aggregate / provider snapshot /
# yfinance) and may be corrected from ONE_DAY. `ANGELONE` rows already came from
# the exchange and are left alone so repeated runs cost nothing.
DERIVED_SOURCES = ("5M_AGG", "BACKFILL", "YFINANCE")

# Test seams: set these to fakes to exercise refresh() without a DB or a network.
_service_override = None       # object exposing is_logged_in / login / get_historical_candles
_session_factory_override = None  # callable returning a SQLAlchemy session
_clock_override = None         # callable returning a naive-IST datetime

_LOG = "[ProviderDaily]"

# In-process work queue. Each pass spends its time on the provider, not on the
# candidate query, so the list is fetched once per cycle and drained. A ticker that
# kept failing is simply retried on the next cycle.
_queue: List[str] = []
_queue_key = None


def _next_batch(db, start_day: date, today: date, limit: int) -> List[str]:
    global _queue, _queue_key
    key = (start_day, today)
    if _queue_key != key:
        _queue, _queue_key = [], key
    if not _queue:
        _queue = candidates(db, start_day, today, max(400, int(limit) * 4))
    batch, _queue = _queue[:int(limit)], _queue[int(limit):]
    return batch


def _today() -> date:
    if _clock_override is not None:
        return _clock_override().date()
    try:
        from database import get_ist_now
        return get_ist_now().date()
    except Exception:
        return date.today()


def _get_service():
    if _service_override is not None:
        return _service_override
    # `historical_service` is an INSTANCE (historical_service = HistoricalDataService()
    # at the bottom of that module), not a module: `historical_service.login()` lives on
    # the class. Importing the module and calling `.login()` raised
    # "module 'historical_service' has no attribute 'login'" the first time this ran,
    # so the refresher silently refused to work. Import the object.
    from historical_service import historical_service
    return historical_service


def _get_session():
    if _session_factory_override is not None:
        return _session_factory_override()
    from database import SessionLocal
    return SessionLocal()


def clean_candle(raw: Any) -> Optional[Dict[str, float]]:
    """Validate one provider candle. Returns None when it must not be written.

    A daily row that is internally impossible (high below the open, low above the
    close, non-positive prices) is worse than a missing row: it silently poisons
    every chart, average and screener that reads it. So the rule is strict.
    """
    if not isinstance(raw, dict):
        return None
    try:
        o = float(raw.get("open", 0) or 0)
        h = float(raw.get("high", 0) or 0)
        l = float(raw.get("low", 0) or 0)
        c = float(raw.get("close", 0) or 0)
        v = float(raw.get("volume", 0) or 0)
    except (TypeError, ValueError):
        return None
    if not (o > 0 and c > 0 and h > 0 and l > 0):
        return None
    # Tolerate a paisa of provider rounding, reject a real inversion.
    if h < max(o, c) - 0.01 or l > min(o, c) + 0.01:
        return None
    if l > h:
        return None
    if v < 0:
        v = 0.0
    return {"open": o, "high": h, "low": l, "close": c, "volume": int(v)}


def select_rows(candles: Iterable[Any], start_day: date, today: date) -> List[Dict[str, Any]]:
    """Keep only in-window, valid candles as ``[{ts, open, high, low, close, volume}]``."""
    out: List[Dict[str, Any]] = []
    for raw in (candles or []):
        if not isinstance(raw, dict):
            continue
        ts = raw.get("timestamp")
        if isinstance(ts, datetime):
            d = ts.date()
        elif isinstance(ts, date):
            d = ts
        elif isinstance(ts, str):
            try:
                d = datetime.fromisoformat(ts[:19]).date()
            except ValueError:
                continue
        else:
            continue
        if d < start_day or d > today:
            continue
        clean = clean_candle(raw)
        if clean is None:
            continue
        clean["ts"] = datetime(d.year, d.month, d.day)  # midnight, the 1D convention
        out.append(clean)
    return out


def candidates(db, start_day: date, today: date, limit: int) -> List[str]:
    """Tickers that still need exchange data anywhere in ``[start_day, today]``.

    Two sets, unioned:
      * rows in the window that still carry a derived source (5M_AGG / BACKFILL /
        YFINANCE);
      * active tickers with NO row at all in the window (never written).

    A ticker whose every row in the window is already ``ANGELONE`` appears in neither,
    so repeated runs finish the universe and then return nothing.

    Shaped deliberately: a plain `timestamp` range (never `timestamp::date`, which
    cannot use `ix_candles_ticker_tf_ts` and made this query take 30 s) plus an
    anti-join for the missing rows. Measured 2026-10-29: 30.5 s -> 2.9 s.
    """
    from sqlalchemy import text as sa_text
    rows = db.execute(sa_text("""
        SELECT ticker FROM (
            SELECT c.ticker AS ticker
              FROM candles c
             WHERE c.timeframe = '1D'
               AND c.timestamp >= :start_ts
               AND c.timestamp < :end_ts
               AND c.data_source IN ('5M_AGG', 'BACKFILL', 'YFINANCE')
             GROUP BY c.ticker
            UNION
            SELECT sm.ticker
              FROM stock_metadata sm
             WHERE sm.is_active
               AND COALESCE(sm.exchange, 'NSE') IN ('NSE', 'BSE')
               AND NOT EXISTS (
                     SELECT 1 FROM candles c2
                      WHERE c2.ticker = sm.ticker
                        AND c2.timeframe = '1D'
                        AND c2.timestamp >= :start_ts
                        AND c2.timestamp < :end_ts)
        ) q
        ORDER BY ticker
        LIMIT :lim
    """), {"start_ts": datetime.combine(start_day, datetime.min.time()),
           "end_ts": datetime.combine(today + timedelta(days=1), datetime.min.time()),
           "lim": int(limit)}).fetchall()
    return [r[0] for r in rows]


def _upsert(db, ticker: str, row: Dict[str, Any]) -> bool:
    from sqlalchemy import text as sa_text
    db.execute(sa_text("""
        INSERT INTO candles (ticker, timeframe, timestamp, open, high, low, close,
                             volume, is_completed, data_source, is_backfilled)
        VALUES (:t, '1D', :ts, :o, :h, :l, :c, :v, true, 'ANGELONE', false)
        ON CONFLICT ON CONSTRAINT uix_candle_key DO UPDATE
           SET open = EXCLUDED.open,
               high = EXCLUDED.high,
               low = EXCLUDED.low,
               close = EXCLUDED.close,
               volume = EXCLUDED.volume,
               is_completed = true,
               data_source = 'ANGELONE',
               is_backfilled = false
    """), {"t": ticker, "ts": row["ts"], "o": row["open"], "h": row["high"],
           "l": row["low"], "c": row["close"], "v": row["volume"]})
    return True


def refresh(limit: int = 120, budget_seconds: float = 60.0, days_back: int = 1,
            only_tickers: Optional[Iterable[str]] = None,
            priority: bool = False) -> Dict[str, Any]:
    """Correct recent 1D rows from AngelOne ``ONE_DAY``. Never raises."""
    started = time.monotonic()
    today = _today()
    start_day = today - timedelta(days=max(0, int(days_back)))
    stats = {"candidates": 0, "tickers": 0, "rows": 0, "skipped": 0,
             "errors": 0, "elapsed": 0.0, "reason": ""}
    db = None
    try:
        svc = _get_service()
        if not getattr(svc, "is_logged_in", False):
            try:
                svc.login()
            except Exception as e:
                print(f"{_LOG} login failed: {e}")
        if not getattr(svc, "is_logged_in", False):
            stats["reason"] = "not_logged_in"
            return stats

        db = _get_session()
        if only_tickers:
            todo = [t for t in only_tickers]
        else:
            todo = _next_batch(db, start_day, today, limit)
        stats["candidates"] = len(todo)

        # The provider drops the window endpoints: measured 2026-10-29,
        # get_historical_candles(ONE_DAY, date(28)..date(28)) returned 0 rows for a day
        # that definitely exists, and date(27)..date(29) returned only the 28th. Asking
        # for a day either side and filtering locally is immune to that off-by-one.
        fetch_from = start_day - timedelta(days=1)
        fetch_to = today + timedelta(days=1)

        for ticker in todo:
            if time.monotonic() - started > float(budget_seconds):
                stats["reason"] = "budget"
                break
            try:
                candles = svc.get_historical_candles(
                    ticker=ticker, interval="ONE_DAY",
                    from_date=fetch_from, to_date=fetch_to,
                    raise_on_error=False, priority=priority)
            except Exception as e:
                stats["errors"] += 1
                print(f"{_LOG} {ticker}: {e}")
                continue
            rows = select_rows(candles, start_day, today)
            if not rows:
                stats["skipped"] += 1
                continue
            wrote = 0
            for row in rows:
                try:
                    _upsert(db, ticker, row)
                    wrote += 1
                except Exception as e:
                    stats["errors"] += 1
                    print(f"{_LOG} {ticker} write: {e}")
                    break
            if wrote:
                try:
                    db.commit()
                except Exception as e:
                    stats["errors"] += 1
                    print(f"{_LOG} {ticker} commit: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    continue
                stats["rows"] += wrote
                stats["tickers"] += 1
            else:
                stats["skipped"] += 1
    except Exception as e:
        stats["reason"] = str(e)[:160]
        print(f"{_LOG} error: {e}")
    finally:
        stats["elapsed"] = round(time.monotonic() - started, 2)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return stats


def main():  # pragma: no cover - manual repair entry point
    """``python daily_provider_refresh.py [TICKER ...]`` -- backfill now.

    NOTE: priority=False deliberately. `priority=True` bypasses the shared limiter, and
    measured 2026-10-29 the broker then answers 0 rows in 0.00 s for every call after the
    first, so a 3-ticker run wrote 1 row and looked like a data problem. Paced calls take
    ~15 s each and always return data.
    """
    import sys
    tickers = sys.argv[1:] or None
    print(refresh(limit=200, budget_seconds=600.0, only_tickers=tickers, priority=False))


if __name__ == "__main__":  # pragma: no cover
    main()
