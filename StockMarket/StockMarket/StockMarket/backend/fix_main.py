"""
Rewrites the broken section of main.py (lines 1667 onwards up to STATIC FILES MOUNT).
Replaces it with corrected get_stock_data_range + all new helper functions.
"""

NEW_CODE = r'''
@app.get("/api/stock-data/range")
def get_stock_data_range(ticker: str = Query(...), range: str = Query("ALL"), db: Session = Depends(get_db)):
    db_tickers = _normalize_ticker(ticker)
    ist_now = database.get_ist_now()
    range_days = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365}.get(range.upper())
    q = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers))
    if range_days is not None:
        cutoff = ist_now.date() - timedelta(days=range_days)
        q = q.filter(models.StockData.date >= cutoff)
    records = q.order_by(models.StockData.date.desc()).limit(5000).all()

    # Fallback to yfinance if insufficient data in DB
    clean_ticker = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    MIN_DAILY_RECORDS = 100 if range.upper() == "ALL" else 30
    if len(records) < MIN_DAILY_RECORDS:
        try:
            yf_ticker = _yfinance_ticker(clean_ticker)
            period_days = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y"}.get(range.upper(), "5y")
            yf_data = yf.download(yf_ticker, period=period_days, interval="1d", progress=False, auto_adjust=True)
            if yf_data is not None and not yf_data.empty:
                records = []
                for idx, row in yf_data.iterrows():
                    try:
                        dt = idx.date() if hasattr(idx, 'date') else idx
                        rec = models.StockData(
                            ticker=clean_ticker, date=dt,
                            open=_safe_float(row.get('Open')),
                            high=_safe_float(row.get('High')),
                            low=_safe_float(row.get('Low')),
                            close=_safe_float(row.get('Close')),
                            adj_close=_safe_float(row.get('Adj Close', row.get('Close'))),
                            volume=_safe_int(row.get('Volume'))
                        )
                        records.append(rec)
                    except Exception:
                        pass
                # Bulk insert fetched data
                try:
                    for rec in records:
                        db.merge(rec)
                    db.commit()
                except Exception:
                    db.rollback()
                # Re-query sorted (most recent first, limit to 5000)
                records = db.query(models.StockData).filter(
                    models.StockData.ticker.in_(db_tickers)
                ).order_by(models.StockData.date.desc()).limit(5000).all()
        except Exception:
            pass

    result = [{"time": str(r.date), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "adj_close": _safe_float(r.adj_close), "volume": _safe_int(r.volume)} for r in records]
    today = ist_now.date()
    live = db.query(models.CurrentDayCandle).filter(
        models.CurrentDayCandle.ticker.in_(db_tickers),
        models.CurrentDayCandle.trading_date == today
    ).first()
    if live:
        result = [r for r in result if r["time"] != str(live.trading_date)]
        result.append({
            "time": str(live.trading_date),
            "open": _safe_float(live.open),
            "high": _safe_float(live.high),
            "low": _safe_float(live.low),
            "close": _safe_float(live.current_price),
            "adj_close": _safe_float(live.current_price),
            "volume": _safe_int(live.volume)
        })
    return result


def _fetch_yfinance_intraday(db, clean_ticker: str, interval: str = "5m"):
    """Fetch intraday data from yfinance and persist to DB."""
    try:
        yf_ticker = _yfinance_ticker(clean_ticker)
        period_map = {"1m": "7d", "5m": "2d", "15m": "1mo", "30m": "1mo", "1h": "1mo"}
        yf_period = period_map.get(interval, "2d")
        yf_int = yf.download(yf_ticker, period=yf_period, interval=interval, progress=False, auto_adjust=True)
        if yf_int is None or yf_int.empty:
            return []
        model_map = {"1m": models.IntradayCandle1Min, "5m": models.IntradayCandle5Min, "15m": models.IntradayCandle15Min}
        target_model = model_map.get(interval, models.IntradayCandle5Min)
        inserted = []
        for idx, row in yf_int.iterrows():
            try:
                ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                if hasattr(idx, 'tz') and idx.tz is not None:
                    ts = ts.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)
                rec = target_model(
                    ticker=clean_ticker, timestamp=ts,
                    open=_safe_float(row.get('Open')), high=_safe_float(row.get('High')),
                    low=_safe_float(row.get('Low')), close=_safe_float(row.get('Close')),
                    volume=_safe_int(row.get('Volume'))
                )
                inserted.append(rec)
            except Exception:
                pass
        try:
            for rec in inserted:
                db.merge(rec)
            db.commit()
        except Exception:
            db.rollback()
        return db.query(target_model).filter(target_model.ticker == clean_ticker).order_by(target_model.timestamp.asc()).limit(5000).all()
    except Exception:
        return []


# NSE session start: 09:15 IST = 33300 seconds past IST midnight
_NSE_SESSION_START_SEC = (9 * 3600) + (15 * 60)


def _ist_aligned_bucket(epoch_sec: int, bucket_minutes: int) -> int:
    """Snap IST epoch-second to NSE session-aligned bucket (09:15 IST start)."""
    bucket_sec = bucket_minutes * 60
    ist_day_start = (epoch_sec // 86400) * 86400
    session_start = ist_day_start + _NSE_SESSION_START_SEC
    offset = epoch_sec - session_start
    if offset < 0:
        return session_start
    return session_start + (offset // bucket_sec) * bucket_sec


def _intraday_cutoff(interval: str):
    """Return IST-naive datetime cutoff for intraday DB queries."""
    ist_now = database.get_ist_now()
    days_back = {"1m": 1, "5m": 1, "15m": 7, "30m": 14, "1h": 30}.get(interval, 1)
    return ist_now - timedelta(days=days_back)


@app.get("/api/stock-data/intraday")
def get_stock_data_intraday(ticker: str = Query(...), interval: str = Query("5m"), after: int = Query(None), db: Session = Depends(get_db)):
    db_tickers = _normalize_ticker(ticker)
    clean_ticker = ticker.strip().upper().replace('.NS', '').replace('.BO', '')
    model_map = {"1m": models.IntradayCandle1Min, "5m": models.IntradayCandle5Min, "15m": models.IntradayCandle15Min}
    model = model_map.get(interval)
    cutoff_dt = _epoch_to_ist_dt(after) if after is not None else _intraday_cutoff(interval)
    if interval in ("30m", "1h"):
        bucket_minutes = 30 if interval == "30m" else 60
        seen_ts = set()
        records = []
        for m in [models.IntradayCandle5Min, models.IntradayCandle15Min]:
            for r in db.query(m).filter(m.ticker.in_(db_tickers), m.timestamp >= cutoff_dt).order_by(m.timestamp.asc()).limit(5000).all():
                ts_key = _ts_to_epoch(r.timestamp)
                if ts_key not in seen_ts:
                    seen_ts.add(ts_key)
                    records.append(r)
        if len(records) < 200:
            _fetch_yfinance_intraday(db, clean_ticker, "15m")
            records = []
            seen_ts = set()
            for m in [models.IntradayCandle5Min, models.IntradayCandle15Min]:
                for r in db.query(m).filter(m.ticker.in_(db_tickers), m.timestamp >= cutoff_dt).order_by(m.timestamp.asc()).limit(5000).all():
                    ts_key = _ts_to_epoch(r.timestamp)
                    if ts_key not in seen_ts:
                        seen_ts.add(ts_key)
                        records.append(r)
        records.sort(key=lambda r: r.timestamp)
        buckets = {}
        for r in records:
            ts = _ts_to_epoch(r.timestamp)
            bk = _ist_aligned_bucket(ts, bucket_minutes)
            if bk not in buckets:
                buckets[bk] = {"time": bk, "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_int(r.volume)}
            else:
                b = buckets[bk]
                b["high"] = max(b["high"], _safe_float(r.high))
                b["low"] = min(b["low"], _safe_float(r.low))
                b["close"] = _safe_float(r.close)
                b["volume"] += _safe_int(r.volume)
        return [v for k, v in sorted(buckets.items())]
    if not model:
        return JSONResponse(status_code=400, content={"error": f"Unsupported interval: {interval}"})
    records = db.query(model).filter(model.ticker.in_(db_tickers), model.timestamp >= cutoff_dt).order_by(model.timestamp.asc()).limit(5000).all()
    if len(records) < 100:
        _fetch_yfinance_intraday(db, clean_ticker, interval)
        records = db.query(model).filter(model.ticker.in_(db_tickers), model.timestamp >= cutoff_dt).order_by(model.timestamp.asc()).limit(5000).all()
    return [{"time": _ts_to_epoch(r.timestamp), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_int(r.volume)} for r in records]


@app.get("/api/stock-data/weekly")
def get_weekly_candles(ticker: str = Query(...), db: Session = Depends(get_db)):
    """Weekly OHLCV candles. One per ISO week, time = Monday UTC midnight epoch."""
    import datetime as _dt
    from collections import defaultdict
    db_tickers = _normalize_ticker(ticker)
    records = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers)).order_by(models.StockData.date.asc()).all()
    if not records:
        return []
    weeks = defaultdict(list)
    for r in records:
        iso = r.date.isocalendar()
        weeks[(iso[0], iso[1])].append(r)
    result = []
    for week_key in sorted(weeks.keys()):
        rows = sorted(weeks[week_key], key=lambda x: x.date)
        monday = _dt.date.fromisocalendar(week_key[0], week_key[1], 1)
        time_epoch = int(_dt.datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp())
        highs = [_safe_float(r.high) for r in rows]
        lows  = [_safe_float(r.low) for r in rows if _safe_float(r.low) > 0]
        result.append({"time": time_epoch, "open": _safe_float(rows[0].open), "high": max(highs) if highs else 0, "low": min(lows) if lows else 0, "close": _safe_float(rows[-1].close), "volume": sum(_safe_int(r.volume) for r in rows)})
    return result


@app.get("/api/stock-data/monthly")
def get_monthly_candles(ticker: str = Query(...), db: Session = Depends(get_db)):
    """Monthly OHLCV candles. One per calendar month, time = first trading day UTC midnight epoch."""
    import datetime as _dt
    from collections import defaultdict
    db_tickers = _normalize_ticker(ticker)
    records = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers)).order_by(models.StockData.date.asc()).all()
    if not records:
        return []
    months = defaultdict(list)
    for r in records:
        months[(r.date.year, r.date.month)].append(r)
    result = []
    for month_key in sorted(months.keys()):
        rows = sorted(months[month_key], key=lambda x: x.date)
        first_day = rows[0].date
        time_epoch = int(_dt.datetime(first_day.year, first_day.month, first_day.day, tzinfo=timezone.utc).timestamp())
        highs = [_safe_float(r.high) for r in rows]
        lows  = [_safe_float(r.low) for r in rows if _safe_float(r.low) > 0]
        result.append({"time": time_epoch, "open": _safe_float(rows[0].open), "high": max(highs) if highs else 0, "low": min(lows) if lows else 0, "close": _safe_float(rows[-1].close), "volume": sum(_safe_int(r.volume) for r in rows)})
    return result


# ==================== PAGINATED / CACHE-OPTIMIZED ENDPOINTS ====================

@app.get("/api/stock-data/candle/latest")
def get_latest_candle(ticker: str = Query(...), interval: str = Query("5m"), db: Session = Depends(get_db)):
    """Return the latest completed candle for WS reconnect candle recovery."""
    db_tickers = _normalize_ticker(ticker)
    model_map = {"1m": models.IntradayCandle1Min, "5m": models.IntradayCandle5Min, "15m": models.IntradayCandle15Min}
    model = model_map.get(interval, models.IntradayCandle5Min)
    record = db.query(model).filter(model.ticker.in_(db_tickers)).order_by(model.timestamp.desc()).first()
    if record:
        return {"time": _ts_to_epoch(record.timestamp), "open": _safe_float(record.open), "high": _safe_float(record.high), "low": _safe_float(record.low), "close": _safe_float(record.close), "volume": _safe_int(record.volume)}
    return {}


@app.get("/api/stock-data/intraday/paginated")
def get_intraday_paginated(ticker: str = Query(...), interval: str = Query("5m"), before: int = Query(None), after: int = Query(None), limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    """Paginated intraday candles."""
    db_tickers = _normalize_ticker(ticker)
    model_map = {"1m": models.IntradayCandle1Min, "5m": models.IntradayCandle5Min, "15m": models.IntradayCandle15Min}
    model = model_map.get(interval)
    if interval in ("30m", "1h"):
        return JSONResponse(status_code=400, content={"error": "Use /api/stock-data/intraday for 30m/1h"})
    if not model:
        return JSONResponse(status_code=400, content={"error": f"Unsupported interval: {interval}"})
    q = db.query(model).filter(model.ticker.in_(db_tickers))
    if before is not None:
        q = q.filter(model.timestamp < _epoch_to_ist_dt(before))
    if after is not None:
        q = q.filter(model.timestamp > _epoch_to_ist_dt(after))
    records = q.order_by(model.timestamp.desc()).limit(limit + 1).all()
    has_more = len(records) > limit
    if has_more:
        records = records[:limit]
    data = [{"time": _ts_to_epoch(r.timestamp), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_int(r.volume)} for r in records]
    data.reverse()
    return {"data": data, "has_more": has_more}


@app.get("/api/stock-data/range/paginated")
def get_range_paginated(ticker: str = Query(...), range: str = Query("ALL"), before: str = Query(None), after: str = Query(None), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Paginated daily candles."""
    db_tickers = _normalize_ticker(ticker)
    q = db.query(models.StockData).filter(models.StockData.ticker.in_(db_tickers))
    if before is not None:
        q = q.filter(models.StockData.date < before)
    if after is not None:
        q = q.filter(models.StockData.date > after)
    range_days = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365}.get(range.upper())
    if range_days is not None and before is None and after is None:
        cutoff = database.get_ist_now().date() - timedelta(days=range_days)
        q = q.filter(models.StockData.date >= cutoff)
    records = q.order_by(models.StockData.date.desc()).limit(limit + 1).all()
    has_more = len(records) > limit
    if has_more:
        records = records[:limit]
    data = [{"time": str(r.date), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "adj_close": _safe_float(r.adj_close), "volume": _safe_int(r.volume)} for r in records]
    data.reverse()
    return {"data": data, "has_more": has_more}


@app.get("/api/stock-data/intraday/since")
def get_intraday_since(ticker: str = Query(...), interval: str = Query("5m"), since: int = Query(None), db: Session = Depends(get_db)):
    """Return all intraday candles since a given timestamp (used for WS reconnect recovery)."""
    if since is None:
        return []
    db_tickers = _normalize_ticker(ticker)
    model_map = {"1m": models.IntradayCandle1Min, "5m": models.IntradayCandle5Min, "15m": models.IntradayCandle15Min}
    model = model_map.get(interval, models.IntradayCandle5Min)
    records = db.query(model).filter(
        model.ticker.in_(db_tickers),
        model.timestamp > _epoch_to_ist_dt(since)
    ).order_by(model.timestamp.asc()).limit(5000).all()
    return [{"time": _ts_to_epoch(r.timestamp), "open": _safe_float(r.open), "high": _safe_float(r.high), "low": _safe_float(r.low), "close": _safe_float(r.close), "volume": _safe_int(r.volume)} for r in records]

'''

# Read main.py
with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line index of _normalize_ticker definition (0-indexed)
# We keep everything up to and INCLUDING _normalize_ticker function (lines 0..1664)
# Then we replace from the @app.get("/api/stock-data/range") line onwards
# until the STATIC FILES MOUNT section

keep_until = None   # line index (0-based) of the last line to keep before range endpoint
replace_from = None # line index of @app.get("/api/stock-data/range")
resume_from = None  # line index of "# ==================== STATIC FILES MOUNT"

for i, line in enumerate(lines):
    stripped = line.strip()
    if '@app.get("/api/stock-data/range")' in line and replace_from is None:
        replace_from = i
        keep_until = i  # exclusive
    if '# ==================== STATIC FILES MOUNT ====================' in line:
        resume_from = i
        break

print(f"keep_until (exclusive) = {keep_until}")
print(f"replace_from = {replace_from}")
print(f"resume_from = {resume_from}")

if keep_until is None or resume_from is None:
    print("ERROR: Could not find markers!")
    exit(1)

# Build new content
before = lines[:keep_until]
after  = lines[resume_from:]

new_content = ''.join(before) + NEW_CODE + '\n' + ''.join(after)

# Syntax check before writing
import ast
try:
    ast.parse(new_content)
    print("SYNTAX OK — writing file")
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Done! Wrote main.py successfully.")
except SyntaxError as e:
    print(f"SYNTAX ERROR at line {e.lineno}: {e.msg}")
    print(f"Context: {e.text}")
