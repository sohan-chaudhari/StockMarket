"""
Monitor
========
Centralized monitoring for the candle system.

Exposes a /api/monitoring/candle-system endpoint with health status
for every component.

Tracks:
  - Live5mBuilder stats (tickers, pending, stale skips, back-pressure)
  - Recovery stats (total, avg duration, errors)
  - LiveTimeframeManager stats (builders, viewers, idle)
  - Cache stats (hit rates, entries)
  - Retention stats (last run, rows converted)
  - EventBus status (subscribers)
  - ExchangeCalendar status (next holiday)
"""

import time
from typing import Dict, Optional

from event_bus import event_bus


class Monitor:
    def __init__(self):
        self._start_time = time.time()
        self._components: Dict[str, callable] = {}

    def register(self, name: str, health_fn: callable):
        self._components[name] = health_fn

    def get_health(self) -> Dict:
        components = {}
        all_healthy = True
        for name, fn in self._components.items():
            try:
                result = fn()
                if isinstance(result, dict):
                    components[name] = result
                    if result.get("status") == "unhealthy":
                        all_healthy = False
                else:
                    components[name] = {"status": "healthy" if result else "unhealthy"}
                    if not result:
                        all_healthy = False
            except Exception as e:
                components[name] = {"status": "unhealthy", "error": str(e)}
                all_healthy = False

        return {
            "uptime_sec": int(time.time() - self._start_time),
            "overall": "healthy" if all_healthy else "degraded",
            "components": components,
        }

    def get_aggregator_health(self, aggregator) -> dict:
        if aggregator is None:
            return {"status": "unhealthy", "error": "No aggregator"}
        try:
            s = aggregator.get_stats()
            return {
                "status": "healthy",
                "active_tickers": s.get("tickers", 0),
                "pending_late_buffer": s.get("pending_late_buffer", 0),
                "pending_flush": s.get("pending_flush", 0),
                "queue_size": s.get("queue_size", 0),
                "backpressure_events": s.get("backpressure_events", 0),
                "stale_skips": sum(s.get("stale_skips", {}).values()),
                "worker_alive": s.get("worker_alive", False),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def get_recovery_health(self, recovery) -> dict:
        if recovery is None:
            return {"status": "unhealthy", "error": "No recovery service"}
        try:
            s = recovery.get_stats()
            return {
                "status": "healthy",
                "recovered_tickers": s.get("recovered_tickers", 0),
                "total_recoveries": s.get("total_recoveries", 0),
                "avg_duration_ms": s.get("avg_duration_ms", 0),
                "errors": s.get("errors", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def get_timeframe_manager_health(self, tf_mgr) -> dict:
        if tf_mgr is None:
            return {"status": "unhealthy", "error": "No timeframe manager"}
        try:
            s = tf_mgr.get_stats()
            return {
                "status": "healthy",
                "total_builders": s.get("total_builders", 0),
                "active_builders": s.get("active_builders", 0),
                "idle_builders": s.get("idle_builders", 0),
                "total_viewers": s.get("total_viewers", 0),
                "tickers_tracked": s.get("tickers_tracked", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def get_cache_health(self, cache) -> dict:
        if cache is None:
            return {"status": "unhealthy", "error": "No cache"}
        try:
            s = cache.get_stats()
            return {
                "status": "healthy",
                "l1_hits": s.get("l1_hits", 0),
                "l2_hits": s.get("l2_hits", 0),
                "l3_hits": s.get("l3_hits", 0),
                "misses": s.get("misses", 0),
                "hit_rate_pct": s.get("hit_rate_pct", 0),
                "l2_entries": s.get("l2_entries", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def get_retention_health(self, retention) -> dict:
        if retention is None:
            return {"status": "unhealthy", "error": "No retention service"}
        try:
            s = retention.get_stats()
            return {
                "status": "healthy",
                "running": s.get("running", False),
                "last_run": s.get("last_run"),
                "rows_converted": s.get("rows_converted", 0),
                "errors": s.get("errors", 0),
                "policy_rules": s.get("policy_rules", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def get_event_bus_health(self) -> dict:
        try:
            return {
                "status": "healthy",
                "subscribers": event_bus.subscriber_count(),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


monitor = Monitor()
