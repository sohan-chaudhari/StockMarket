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

_LOG = "[ProviderDaily]"


def _get_service():
    if _service_override is not None:
        return _service_override
    import historical_service  # imported lazily so tests need no credentials
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


def candidates(db, today: date, limit: int) -> List[str]:
    """Tickers whose ``today`` 1D row is missing or still derived."""
    from sqlalchemy import text as sa_text
    rows = db.execute(sa_text("""
        SELECT sm.ticker
        FROM stock_metadata sm
        LEFT JOIN candles c
               ON c.ticker = sm.ticker
              AND c.timeframe = '1D'
              AND c.timestamp::date = :today
        WHERE sm.is_active
          AND COALESCE(sm.exchange, 'NSE') IN ('NSE', 'BSE')
          AND (c.ticker IS NULL OR c.data_source IN ('5M_AGG', 'BACKFILL', 'YFINANCE'))
        GROUP BY sm.ticker
        ORDER BY (bool_or(c.ticker IS NULL)) DESC, sm.ticker
        LIMIT :lim
    """), {"today": today, "lim": int(limit)}).fetchall()
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
    # Local import of the IST clock keeps this module test-friendly.
    try:
        from database import get_ist_now
        today = get_ist_now().date()
    except Exception:
        today = date.today()
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
            todo = candidates(db, today, limit)
        stats["candidates"] = len(todo)

        for ticker in todo:
            if time.monotonic() - started > float(budget_seconds):
                stats["reason"] = "budget"
                break
            try:
                candles = svc.get_historical_candles(
                    ticker=ticker, interval="ONE_DAY",
                    from_date=start_day, to_date=today,
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
    """``python daily_provider_refresh.py [TICKER ...]`` -- backfill now, ignoring the queue."""
    import sys
    tickers = sys.argv[1:] or None
    print(refresh(limit=200, budget_seconds=180.0, only_tickers=tickers, priority=True))


if __name__ == "__main__":  # pragma: no cover
    main()
