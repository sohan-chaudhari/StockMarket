from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from config.timeframe_registry import TIMEFRAME_REGISTRY
from exchange_calendar import IST

SOURCE_HIERARCHY = ["5m", "15m", "30m", "1h", "4h", "1d", "1w", "1m"]


class ChartService:
    def __init__(self, live_mgr, cache, resample_svc, db_session_factory, candle_aggregator=None):
        self._live_mgr = live_mgr
        self._cache = cache
        self._resample_svc = resample_svc
        self._db_factory = db_session_factory
        self._candle_aggregator = candle_aggregator

    def get_chart(self, ticker: str, timeframe: str, start: Optional[int] = None,
                  end: Optional[int] = None, limit: int = 5000) -> Dict:
        tf_config = TIMEFRAME_REGISTRY.get(timeframe)
        if tf_config is None:
            return {"candles": [], "metadata": {"error": f"Unknown timeframe: {timeframe}"}}

        was_resampled = False
        stored_until = None
        has_live = False
        has_forming = False
        source = "db"

        live_snapshot = None
        if self._live_mgr is not None and timeframe != "5m":
            live_snapshot = self._live_mgr.get_current(ticker, timeframe)
            if live_snapshot:
                has_live = True
        elif self._candle_aggregator is not None and timeframe == "5m":
            # For 5m, use the raw candle_aggregator which provides the base 5m forming candle
            agg_live = self._candle_aggregator.get_current(ticker)
            if agg_live and "5m" in agg_live:
                live_snapshot = {"forming": agg_live["5m"], "completed": []}
                has_live = True

        cache_key_start = start
        cache_key_end = end
        cached = self._cache.get(ticker, timeframe, cache_key_start, cache_key_end)

        if cached:
            source = "cache"
            result = cached
        else:
            result = self._build_candles(ticker, timeframe, start, end, limit)
            if result:
                was_resampled = True
                source = result.get("_source", "db")
                stored_until = result.get("_stored_until")
                result = result.get("candles", [])
                self._cache.set(ticker, timeframe, result, start=start, end=end)
            else:
                result = []

        if live_snapshot:
            completed = live_snapshot.get("completed", [])
            forming = live_snapshot.get("forming")
            has_forming = forming is not None
            ts_set = {c.get("time") for c in result}
            for c in completed:
                if c.get("time") not in ts_set:
                    result.append(c)
            if forming and forming.get("time") not in ts_set:
                has_live = True
                result.append(forming)
            result.sort(key=lambda c: c.get("time", 0))

        if result and timeframe != "5m":
            result = self._deduplicate(result)

        return {
            "candles": result,
            "metadata": {
                "live": has_live,
                "forming_candle": has_forming,
                "stored_until": stored_until,
                "resampled_from": None,
                "source": source,
                "count": len(result),
            },
        }

    def _build_candles(self, ticker: str, target_tf: str,
                       start: Optional[int], end: Optional[int],
                       limit: int) -> Optional[Dict]:
        db = self._db_factory()
        try:
            from models import IntradayCandle1Min, IntradayCandle5Min, IntradayCandle15Min, IntradayCandle30Min, IntradayCandle1H, StockData, Candle

            def _fetch_tf(tf: str, query_limit: int = None):
                if query_limit is None:
                    query_limit = limit
                model = None
                if tf == "1m":
                    model = IntradayCandle1Min
                elif tf == "5m":
                    model = IntradayCandle5Min
                elif tf == "15m":
                    model = IntradayCandle15Min
                elif tf == "30m":
                    model = IntradayCandle30Min
                elif tf == "1h":
                    model = IntradayCandle1H
                elif tf in ["1d", "1D"]:
                    model = StockData
                
                if not model:
                    # Query unified Candle table for 4h, 1w, 1m, etc.
                    q = db.query(Candle).filter(Candle.ticker == ticker, Candle.timeframe == tf)
                    if start:
                        q = q.filter(Candle.timestamp >= datetime.fromtimestamp(start, tz=IST).replace(tzinfo=None))
                    if end:
                        q = q.filter(Candle.timestamp <= datetime.fromtimestamp(end, tz=IST).replace(tzinfo=None))
                    if not start:
                        res = q.order_by(Candle.timestamp.desc()).limit(query_limit).all()
                        return list(reversed(res))
                    return q.order_by(Candle.timestamp.asc()).limit(query_limit).all()
                
                q = db.query(model).filter(model.ticker == ticker)
                
                if model == StockData:
                    # StockData uses 'date' column
                    if start:
                        q = q.filter(model.date >= datetime.fromtimestamp(start, tz=IST).date())
                    if end:
                        q = q.filter(model.date <= datetime.fromtimestamp(end, tz=IST).date())
                    if not start:
                        res = q.order_by(model.date.desc()).limit(query_limit).all()
                        return list(reversed(res))
                    return q.order_by(model.date.asc()).limit(query_limit).all()
                else:
                    # Intraday tables use 'timestamp'
                    if start:
                        q = q.filter(model.timestamp >= datetime.fromtimestamp(start, tz=IST).replace(tzinfo=None))
                    if end:
                        q = q.filter(model.timestamp <= datetime.fromtimestamp(end, tz=IST).replace(tzinfo=None))
                    if not start:
                        res = q.order_by(model.timestamp.desc()).limit(query_limit).all()
                        return list(reversed(res))
                    return q.order_by(model.timestamp.asc()).limit(query_limit).all()

            exact_stored = _fetch_tf(target_tf)

            if target_tf == "5m":
                if not exact_stored:
                    return None
                return {
                    "candles": self._stored_to_dict(exact_stored),
                    "_source": "db",
                    "_stored_until": self._last_ts(exact_stored),
                }

            target_idx = SOURCE_HIERARCHY.index(target_tf)
            # Optimize: ONLY pull from the immediate lower timeframe to build the target chart!
            source_tfs = [SOURCE_HIERARCHY[target_idx - 1]]

            all_resampled_dicts = []

            for src_tf in source_tfs:
                src_candles = _fetch_tf(src_tf, query_limit=limit * 60)
                if not src_candles:
                    continue

                src_dicts = []
                for r in src_candles:
                    ts = getattr(r, "timestamp", getattr(r, "date", None))
                    # if it's a python date but not a datetime, convert to datetime for resampling
                    if ts and not isinstance(ts, datetime):
                        ts = datetime.combine(ts, datetime.min.time())
                    
                    src_dicts.append({
                        "timestamp": ts,
                        "open": r.open, "high": r.high,
                        "low": r.low, "close": r.close,
                        "volume": r.volume,
                    })

                try:
                    target = self._resample_svc.resample_5m_to(src_dicts, target_tf)
                except Exception as e:
                    print(f"Resampling error {src_tf} -> {target_tf}: {e}")
                    target = None

                if not target:
                    continue

                for c in target:
                    ts = c.get("timestamp")
                    if isinstance(ts, datetime):
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=IST)
                        ts_epoch = int(ts.timestamp())
                    elif isinstance(ts, (int, float)):
                        ts_epoch = int(ts)
                    else:
                        continue
                    if start and ts_epoch < start:
                        continue
                    if end and ts_epoch > end:
                        continue
                    all_resampled_dicts.append({
                        "time": ts_epoch,
                        "open": c.get("open", 0),
                        "high": c.get("high", 0),
                        "low": c.get("low", 0),
                        "close": c.get("close", 0),
                        "volume": c.get("volume", 0),
                    })

            stored_dicts = self._stored_to_dict(exact_stored)
            stored_times = {c["time"] for c in stored_dicts}

            for c in all_resampled_dicts:
                if c["time"] not in stored_times:
                    stored_dicts.append(c)

            stored_dicts.sort(key=lambda c: c["time"])

            if not stored_dicts:
                return None

            source_label = "resampled" if all_resampled_dicts else "db"
            if exact_stored and all_resampled_dicts:
                source_label = "mixed"

            return {
                "candles": stored_dicts,
                "_source": source_label,
                "_stored_until": self._last_ts(exact_stored) if exact_stored else None,
            }

        except Exception as e:
            print(f"[ChartService] _build_candles error for {ticker} {target_tf}: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            db.close()

    def _last_ts(self, candles) -> Optional[int]:
        if not candles:
            return None
        last = candles[-1]
        if hasattr(last, 'timestamp') and last.timestamp:
            return int(last.timestamp.timestamp()) if hasattr(last.timestamp, 'timestamp') else None
        return None

    def _stored_to_dict(self, stored: List) -> List[Dict]:
        result = []
        for r in stored:
            ts = getattr(r, "timestamp", getattr(r, "date", None))
            if ts and not isinstance(ts, datetime):
                ts = datetime.combine(ts, datetime.min.time())
            
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=IST)
                ts_epoch = int(ts.timestamp())
            else:
                ts_epoch = int(ts) if ts else 0
            result.append({
                "time": ts_epoch,
                "open": float(r.open) if r.open else 0,
                "high": float(r.high) if r.high else 0,
                "low": float(r.low) if r.low else 0,
                "close": float(r.close) if r.close else 0,
                "volume": int(r.volume) if r.volume else 0,
            })
        return result

    def _deduplicate(self, candles: List[Dict]) -> List[Dict]:
        seen = set()
        result = []
        for c in candles:
            t = c.get("time", 0)
            if t not in seen:
                seen.add(t)
                result.append(c)
        return result
