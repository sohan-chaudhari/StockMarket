"""
Multi-Threaded Price Poller
============================
Fetches live prices for ALL stocks in parallel via AngelOne REST API.
Uses a semaphore to respect AngelOne rate limits (~10 req/s).
"""

import time
import json
import os
import asyncio
import concurrent.futures
import threading
from typing import List

MAX_WORKERS = 50
API_CONCURRENCY = 8
CYCLE_INTERVAL = 2.0
CYCLE_MAX_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_MULTIPLIER = 2.0
MAX_BACKOFF_INTERVAL = 60.0

class PricePoller:
    def __init__(self, angelone_service, tickers=None):
        self.service = angelone_service
        self.all_tickers: List[str] = tickers or []
        self._token_cache = {}
        self._running = False
        self._executor = None
        self._stats = {"cycles": 0, "success": 0, "fail": 0}
        self._consecutive_failures = 0
        self._current_interval = CYCLE_INTERVAL
        self._api_sem = threading.Semaphore(API_CONCURRENCY)
        self._stats_lock = threading.Lock()

    def load_tickers(self) -> int:
        print(f"[PricePoller] Using {len(self.all_tickers)} pre-configured tickers")
        return len(self.all_tickers)

    async def start(self):
        self._running = True
        count = self.load_tickers()
        if count == 0:
            print("[PricePoller] No tickers loaded — idle")
            return

        if not self.service.is_logged_in:
            print("[PricePoller] Logging in to AngelOne...")
            self.service.login()
        if not self.service.is_logged_in:
            print("[PricePoller] AngelOne login failed — poller unavailable")
            return

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
        loop = asyncio.get_running_loop()
        print(f"[PricePoller] Started ({MAX_WORKERS} workers, {len(self.all_tickers)} stocks)")

        while self._running:
            try:
                cycle_ok = await self._run_cycle(loop)
            except Exception as e:
                print(f"[PricePoller] Cycle error: {e}")
                cycle_ok = False

            if cycle_ok:
                self._consecutive_failures = 0
                self._current_interval = CYCLE_INTERVAL
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._current_interval = min(
                        self._current_interval * BACKOFF_MULTIPLIER,
                        MAX_BACKOFF_INTERVAL
                    )
                    if self._consecutive_failures == MAX_CONSECUTIVE_FAILURES:
                        print(f"[PricePoller] Circuit breaker: {self._consecutive_failures} consecutive failures, backing off to {self._current_interval:.0f}s")

            await asyncio.sleep(self._current_interval)

    async def _run_cycle(self, loop) -> bool:
        t0 = time.time()
        success = 0
        fail = 0
        CYCLE_MAX_SECONDS = 30

        futures = []
        for ticker in self.all_tickers:
            future = loop.run_in_executor(self._executor, self._poll_one, ticker)
            futures.append(future)

        done, pending = await asyncio.wait(futures, timeout=CYCLE_MAX_SECONDS)
        for coro in done:
            ok = await coro
            if ok:
                success += 1
            else:
                fail += 1

        # Cancel remaining (they'll be retried next cycle)
        for f in pending:
            f.cancel()

        elapsed = time.time() - t0
        with self._stats_lock:
            self._stats["cycles"] += 1
            self._stats["success"] += success
            self._stats["fail"] += fail

        if self._stats["cycles"] % 3 == 0:
            pct = (success / (success + fail)) * 100 if (success + fail) > 0 else 0
            remaining = len(pending)
            print(f"[PricePoller] Cycle #{self._stats['cycles']}: {success} ok, {fail} fail ({pct:.0f}%) {remaining} pending in {elapsed:.1f}s")

        return success > 0

    def _poll_one(self, ticker: str) -> bool:
        try:
            svc = self.service
            if not svc.is_logged_in or not svc.smart_api:
                return False

            if ticker not in self._token_cache:
                inst = svc.get_token(ticker)
                if not inst:
                    return False
                self._token_cache[ticker] = inst
            instrument = self._token_cache[ticker]

            with self._api_sem:
                raw = svc.smart_api.ltpData(
                    exchange=instrument["exchange"],
                    tradingsymbol=instrument["symbol"],
                    symboltoken=instrument["token"]
                )
            time.sleep(0.15)  # pace: stay under rate limit

            if not raw.get("status"):
                return False

            d = raw.get("data", {})
            ltp = float(d.get("ltp", 0))
            if ltp <= 0:
                return False

            t = ticker.upper()
            pc = float(d.get("close", 0))
            change = round(ltp - pc, 2) if pc else 0
            change_pct = round(((ltp - pc) / pc) * 100, 2) if pc and pc != 0 else 0

            with svc.latest_ticks_lock:
                existing = svc.latest_ticks.get(t, {})
                svc.latest_ticks[t] = {
                    "current_price": ltp,
                    "current": ltp,
                    "open": float(d.get("open", 0)),
                    "high": float(d.get("high", 0)),
                    "low": float(d.get("low", 0)),
                    "prev_close": pc,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": existing.get("volume", 0),
                    "time": time.time(),
                }
            return True

        except Exception:
            return False

    def stop(self):
        self._running = False
        if self._executor:
            self._executor.shutdown(wait=False)

    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)
