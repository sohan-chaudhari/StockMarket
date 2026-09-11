"""
Batch 1D Candle Backfill Service.

Efficiently backfills daily candles across all tickers in stock_metadata up to the
latest completed trading session using batched multi-threaded yfinance with Angel One fallback.

Usage:
    python batch_backfill_all.py
"""

import math
import os
import sys
import time
from datetime import datetime, date, timedelta, timezone

_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import yfinance as yf
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from database import SessionLocal
from models import StockMetadata, Candle
from historical_service import historical_service

IST = timezone(timedelta(hours=5, minutes=30))
BATCH_SIZE = 50

NSE_HOLIDAYS_SET = {
    date(2026, 1, 26), date(2026, 3, 6), date(2026, 3, 20), date(2026, 4, 3),
    date(2026, 4, 14), date(2026, 5, 1), date(2026, 8, 15), date(2026, 10, 2),
    date(2026, 10, 20), date(2026, 11, 10), date(2026, 12, 25)
}


def _safe_float(v, default=0.0):
    if v is None:
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return default


def _safe_int(v, default=0):
    if v is None:
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else int(f)
    except (ValueError, TypeError):
        return default


def get_target_trading_day(now_dt: datetime = None) -> date:
    if now_dt is None:
        now_dt = datetime.now(IST)
    d = now_dt.date()
    if d.weekday() < 5 and d not in NSE_HOLIDAYS_SET and now_dt.time() >= datetime.strptime("15:30", "%H:%M").time():
        return d
    cur = d - timedelta(days=1)
    while cur.weekday() >= 5 or cur in NSE_HOLIDAYS_SET:
        cur -= timedelta(days=1)
    return cur


def run_batch_backfill():
    db = SessionLocal()
    target_day = get_target_trading_day()
    print(f"=== Starting Batch 1D Backfill (Target Date: {target_day}) ===")

    try:
        # 1. Query all stocks
        all_stocks = db.query(StockMetadata.ticker, StockMetadata.exchange).filter(StockMetadata.is_active == True).all()
        all_ticker_map = {t: ex for t, ex in all_stocks}
        total_tickers = len(all_ticker_map)

        # 2. Query tickers already having target_day
        have_target_rows = db.execute(text(
            "SELECT DISTINCT ticker FROM candles WHERE timeframe = '1D' AND timestamp::date = :td AND close > 0 AND open > 0"
        ), {"td": target_day}).fetchall()
        have_target = {r[0] for r in have_target_rows}

        missing_tickers = [t for t in all_ticker_map if t not in have_target]
        print(f"Total active tickers: {total_tickers} | Up-to-date: {len(have_target)} | Missing target: {len(missing_tickers)}")

        if not missing_tickers:
            print("All tickers are already up to date!")
            return

        total_upserted = 0
        failed_tickers = []
        batches = [missing_tickers[i:i + BATCH_SIZE] for i in range(0, len(missing_tickers), BATCH_SIZE)]

        for b_idx, batch in enumerate(batches, 1):
            yf_map = {}
            for t in batch:
                ex = all_ticker_map.get(t, "NSE")
                suffix = ".BO" if ex == "BSE" else ".NS"
                yf_map[f"{t}{suffix}"] = (t, ex)

            print(f"[{b_idx}/{len(batches)}] Downloading batch of {len(batch)} tickers...", end=" ", flush=True)
            batch_start = time.time()
            try:
                data = yf.download(list(yf_map.keys()), period="1mo", interval="1d", group_by="ticker", threads=True, timeout=12, progress=False)
            except Exception as e:
                print(f"yfinance error: {e}")
                data = None

            batch_upserted = 0
            batch_missing_after_yf = []

            for ytk, (t, ex) in yf_map.items():
                got_target = False
                if data is not None and ytk in data:
                    df = data[ytk]
                    if not df.empty:
                        for idx, row in df.iterrows():
                            o = _safe_float(row.get("Open"))
                            h = _safe_float(row.get("High"))
                            l = _safe_float(row.get("Low"))
                            c = _safe_float(row.get("Close"))
                            v = _safe_int(row.get("Volume"))
                            if o > 0 and c > 0 and h > 0 and l > 0:
                                dt = idx.date() if hasattr(idx, "date") else idx
                                if dt == target_day:
                                    got_target = True
                                ts = datetime(dt.year, dt.month, dt.day, 0, 0, 0)
                                db.execute(pg_insert(Candle).values(
                                    ticker=t, timeframe="1D", timestamp=ts,
                                    open=o, high=max(o, h, c), low=min(o, l, c), close=c, volume=v,
                                    is_completed=True, data_source="YFINANCE", is_backfilled=True
                                ).on_conflict_do_update(
                                    constraint="uix_candle_key",
                                    set_={"open": o, "high": max(o, h, c), "low": min(o, l, c), "close": c, "volume": v, "is_completed": True, "data_source": "YFINANCE"}
                                ))
                                batch_upserted += 1

                if not got_target:
                    batch_missing_after_yf.append((t, ex))

            db.commit()

            # Angel One fallback for missing target tickers in this batch
            if batch_missing_after_yf:
                if not historical_service.is_logged_in:
                    historical_service.login()
                if historical_service.is_logged_in:
                    for t, ex in batch_missing_after_yf:
                        try:
                            time.sleep(0.4)  # Prevent Angel One rate limiting
                            angel_candles = historical_service.get_historical_candles(
                                ticker=t, interval="ONE_DAY",
                                from_date=target_day - timedelta(days=30),
                                to_date=target_day,
                                exchange=ex or "NSE"
                            )
                            if angel_candles:
                                for ac in angel_candles:
                                    ts_raw = ac.get("timestamp")
                                    if isinstance(ts_raw, datetime):
                                        o = _safe_float(ac.get("open"))
                                        h = _safe_float(ac.get("high"))
                                        l = _safe_float(ac.get("low"))
                                        c = _safe_float(ac.get("close"))
                                        v = _safe_int(ac.get("volume"))
                                        if o > 0 and c > 0:
                                            ts = datetime(ts_raw.year, ts_raw.month, ts_raw.day, 0, 0, 0)
                                            db.execute(pg_insert(Candle).values(
                                                ticker=t, timeframe="1D", timestamp=ts,
                                                open=o, high=max(o, h, c), low=min(o, l, c), close=c, volume=v,
                                                is_completed=True, data_source="ANGELONE", is_backfilled=True
                                            ).on_conflict_do_update(
                                                constraint="uix_candle_key",
                                                set_={"open": o, "high": max(o, h, c), "low": min(o, l, c), "close": c, "volume": v, "is_completed": True, "data_source": "ANGELONE"}
                                            ))
                                            batch_upserted += 1
                                db.commit()
                        except Exception:
                            failed_tickers.append(t)
                else:
                    failed_tickers.extend([t for t, _ in batch_missing_after_yf])

            total_upserted += batch_upserted
            elapsed = time.time() - batch_start
            print(f"done (+{batch_upserted} candles, {elapsed:.1f}s)")
            time.sleep(0.5)

        print(f"\n=== Batch Backfill Completed! ===")
        print(f"Total candle records upserted: {total_upserted}")
        if failed_tickers:
            print(f"Tickers with no data (delisted or inactive): {len(failed_tickers)}")

    finally:
        db.close()


if __name__ == "__main__":
    run_batch_backfill()
