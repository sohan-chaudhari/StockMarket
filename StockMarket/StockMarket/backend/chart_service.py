from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from config.timeframe_registry import TIMEFRAME_REGISTRY
from exchange_calendar import IST

# Decision/Candidate A fix: must match config.timeframe_registry.TIMEFRAME_REGISTRY's
# actual casing exactly ("1D"/"1W"/"1M", uppercase for session/week/month tiers) —
# the lowercase entries previously here ("1d"/"1w"/"1m") could never match a real
# target_tf value coming from the registry, so SOURCE_HIERARCHY.index(target_tf)
# always raised ValueError for those three timeframes, silently caught by the
# broad except in _build_candles and returned as empty candles.
SOURCE_HIERARCHY = ["5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"]

# Decision 6 fix: the caller-supplied `limit` is now bounded at the API layer
# (main.py's /api/stock-data/chart route, le=50000 — matching the ceiling the
# sibling paginated endpoints already use), but _build_candles derives a
# SEPARATE source-tier query size (`limit * 60` below) to have enough lower-
# timeframe rows to resample from. That derived size must have its own fixed
# ceiling too, independent of how large the caller's `limit` is allowed to be,
# or a large-but-"reasonable" limit on a coarse target timeframe could still
# explode into an unbounded query against the 5m/lower-tier table.
MAX_SOURCE_QUERY_ROWS = 50000


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
            from models import Candle

            def _fetch_tf(tf: str, query_limit: int = None):
                if query_limit is None:
                    query_limit = limit
                query_limit = min(query_limit, MAX_SOURCE_QUERY_ROWS)
                # All timeframes (including 1D) read from the unified Candle table.
                # candles(1D) is the SSOT; the old StockData path for "1d"/"1D" is removed.
                q = db.query(Candle).filter(Candle.ticker == ticker, Candle.timeframe == tf)
                if start:
                    q = q.filter(Candle.timestamp >= datetime.fromtimestamp(start, tz=IST).replace(tzinfo=None))
                if end:
                    q = q.filter(Candle.timestamp <= datetime.fromtimestamp(end, tz=IST).replace(tzinfo=None))
                if not start:
                    res = q.order_by(Candle.timestamp.desc()).limit(query_limit).all()
                    return list(reversed(res))
                return q.order_by(Candle.timestamp.asc()).limit(query_limit).all()

            exact_stored = _fetch_tf(target_tf)

            if target_tf == "5m":
                if not exact_stored:
                    return None
                return {
                    "candles": self._stored_to_dict(exact_stored),
                    "_source": "db",
                    "_stored_until": self._last_ts(exact_stored),
                }

            # Fast Path: If exact_stored is sufficient, bypass all lower-tier DB queries and Python resampling
            if self._is_exact_sufficient(exact_stored, target_tf, start, end, limit):
                return {
                    "candles": self._stored_to_dict(exact_stored),
                    "_source": "db",
                    "_stored_until": self._last_ts(exact_stored),
                }

            target_idx = SOURCE_HIERARCHY.index(target_tf)
            # Fallback through source hierarchy: try from the immediate lower TF down to 5m.
            # Highest TFs win on duplicate timestamps (prefer already-aggregated data).
            source_tfs = list(reversed(SOURCE_HIERARCHY[:target_idx]))

            all_resampled_dicts = []
            seen_resampled_times = set()

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

                added_this_tf = 0
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
                    # Highest TF wins: skip if already filled by a higher TF
                    if ts_epoch in seen_resampled_times:
                        continue
                    seen_resampled_times.add(ts_epoch)
                    added_this_tf += 1
                    all_resampled_dicts.append({
                        "time": ts_epoch,
                        "open": c.get("open", 0),
                        "high": c.get("high", 0),
                        "low": c.get("low", 0),
                        "close": c.get("close", 0),
                        "volume": c.get("volume", 0),
                    })
                if added_this_tf:
                    print(f"[ChartService] {ticker} {target_tf}: +{added_this_tf} candles resampled from {src_tf}")

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

    def _is_exact_sufficient(self, exact_stored: List, target_tf: str,
                              start: Optional[int], end: Optional[int],
                              limit: int) -> bool:
        """
        Determines whether exact_stored from the candles table completely satisfies
        the requested range/limit.

        Rules:
        1. If exact_stored is empty: False (must fallback to lower tiers).
        2. If target_tf is a macro/session tier ("1D", "1W", "1M"):
           Candles table is the SSOT back to inception; lower tiers cannot yield older data -> True.
        3. If explicit start range is requested:
           - First stored candle must start at or before `start` (with 1-period tolerance).
           - If `end` is specified, last stored candle must reach `end` (or current completed bar).
           - If range is not fully covered by exact_stored -> False (reconstruct missing history from 5m).
        4. If limit-based request (start is None):
           - If len(exact_stored) >= limit -> True (full requested count satisfied).
           - If len(exact_stored) < limit -> False (lower tiers may contain older 5m data to reach limit).
        """
        if not exact_stored:
            return False

        count = len(exact_stored)

        # Macro / session tiers (1D, 1W, 1M) are the primary SSOT back to stock inception
        if target_tf in ("1D", "1W", "1M"):
            return True

        # Explicit range query (start timestamp specified)
        if start is not None:
            first_ts = getattr(exact_stored[0], "timestamp", getattr(exact_stored[0], "date", None))
            if not first_ts:
                return False
            if isinstance(first_ts, datetime):
                first_epoch = int(first_ts.replace(tzinfo=IST).timestamp() if first_ts.tzinfo is None else first_ts.timestamp())
            else:
                first_epoch = int(first_ts)

            # Tolerance for market closures (weekends, holidays, overnight sessions: up to 4 calendar days)
            calendar_tolerance = 4 * 86400

            # If the earliest stored candle is more than 4 days after the requested start,
            # exact_stored does not cover the historical start -> reconstruct from lower tiers
            if first_epoch > start + calendar_tolerance:
                return False

            if end is not None:
                last_ts = getattr(exact_stored[-1], "timestamp", getattr(exact_stored[-1], "date", None))
                if last_ts:
                    if isinstance(last_ts, datetime):
                        last_epoch = int(last_ts.replace(tzinfo=IST).timestamp() if last_ts.tzinfo is None else last_ts.timestamp())
                    else:
                        last_epoch = int(last_ts)
                    # End tolerance: 4 days for weekend/holiday closed periods
                    if last_epoch < end - calendar_tolerance:
                        return False

            return True

        # Limit-based query (start is None)
        # If exact_stored returned the full requested limit, the limit is 100% satisfied
        if count >= limit:
            return True

        # If count < limit for an intraday timeframe, lower tiers (e.g. 5m) might have older data
        # to satisfy the remainder of the limit -> do NOT short-circuit
        return False
