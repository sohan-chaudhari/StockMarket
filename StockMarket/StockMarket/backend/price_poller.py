"""
Multi-Threaded Price Poller
============================
Fetches live prices for ALL stocks in parallel via AngelOne REST API.
Uses a semaphore to respect AngelOne rate limits (~10 req/s).

CriticalIndexPoller
====================
A dedicated high-priority thread that polls ONLY major indices every 1s.
This guarantees NIFTY / SENSEX / BANKNIFTY are NEVER more than ~1s stale,
matching what you see in the Angel One app.
"""

import time
import json
import os
import asyncio
import concurrent.futures
import threading
from typing import List

MAX_WORKERS = 8        # matches API_CONCURRENCY — extra threads above this only idle on the semaphore
API_CONCURRENCY = 8
CYCLE_INTERVAL = 2.0
CYCLE_MAX_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_MULTIPLIER = 2.0
MAX_BACKOFF_INTERVAL = 60.0

# Critical indices polled on a fast dedicated thread
CRITICAL_INDICES = ['NIFTY', 'SENSEX', 'BANKNIFTY', 'FINNIFTY', 'MIDCAP', 'SMALLCAP']
CRITICAL_POLL_INTERVAL = 0.5  # seconds between each poll round (was 1.0 — halved for faster index updates)

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

            if not raw.get("status"):
                # Same token-missing/unauthorized detection used by
                # angelone_service.get_live_price() -- without this, a
                # broker-invalidated session (e.g. from a concurrent login
                # under the same account) reports every poll as a generic
                # failure forever, since is_logged_in never gets updated.
                msg = str(raw.get("message", "")).lower()
                err_code = raw.get("errorCode", "")
                if "token missing" in msg or err_code == "AG8003" or "unauthorized" in msg:
                    svc.is_logged_in = False
                return False

            d = raw.get("data", {})
            ltp = float(d.get("ltp", 0))
            if ltp <= 0:
                return False

            t = ticker.upper()
            pc = float(d.get("close", 0))
            change = round(ltp - pc, 2) if pc else 0
            change_pct = round(((ltp - pc) / pc) * 100, 2) if pc and pc != 0 else 0
            now_ts = time.time()

            with svc.latest_ticks_lock:
                existing = svc.latest_ticks.get(t, {})
                # Skip overwriting if WS has provided a real-time update in the last 5 seconds
                # WS is always fresher than REST polling
                ws_received_ts = existing.get("_received_ts", 0)
                if now_ts - ws_received_ts < 5 and existing.get("_source") == "angel_ws":
                    return True

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
                    "_ts": now_ts,
                    "_received_ts": now_ts,
                    "_source": "angel_rest_poller",
                    "time": now_ts,
                }
                if svc.on_tick_callback:
                    svc.on_tick_callback(t, svc.latest_ticks[t])
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


class CriticalIndexPoller:
    """
    Dedicated high-priority thread that polls ONLY the 6 major indices
    (NIFTY, SENSEX, BANKNIFTY, FINNIFTY, MIDCAP, SMALLCAP) every ~1 second
    via the AngelOne REST API.

    This guarantees the dashboard shows the same price you see in the
    Angel One app, with no more than ~1-2 seconds lag.

    It runs independently of the main PricePoller so bulk-stock polling
    never delays a NIFTY update.
    """

    # RT-10: circuit breaker, matching the pattern already used by
    # PricePoller -- without this, an Angel One outage makes this thread
    # hammer the REST API every ~1s indefinitely (6 requests/cycle) since
    # nothing here ever slows it down.
    MAX_CONSECUTIVE_FAILURES = 5
    BACKOFF_MULTIPLIER = 2.0
    MAX_BACKOFF_INTERVAL = 30.0

    def __init__(self, angelone_service, on_price_update=None):
        self.service = angelone_service
        self.on_price_update = on_price_update  # callback: fn(ticker, data_dict)
        self._token_cache = {}
        self._thread = None
        self._stop_event = threading.Event()
        self._stats = {"polls": 0, "updates": 0, "errors": 0}
        self._consecutive_cycle_failures = 0
        self._current_interval = CRITICAL_POLL_INTERVAL

    def start(self):
        """Start the dedicated background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="CriticalIndexPoller",
            daemon=True
        )
        self._thread.start()
        print(f"[CriticalIndexPoller] Started — polling {CRITICAL_INDICES} every {CRITICAL_POLL_INTERVAL}s")

    def stop(self):
        self._stop_event.set()

    def _resolve_tokens(self):
        """Pre-resolve tokens for all critical indices."""
        svc = self.service
        for ticker in CRITICAL_INDICES:
            if ticker in self._token_cache:
                continue
            try:
                inst = svc.get_token(ticker)
                if inst:
                    self._token_cache[ticker] = inst
            except Exception:
                pass

    def _poll_ticker(self, ticker: str) -> bool:
        """Poll one ticker via REST. Returns True if price was updated."""
        svc = self.service
        if not svc.is_logged_in or not svc.smart_api:
            return False

        instrument = self._token_cache.get(ticker)
        if not instrument:
            return False

        try:
            raw = svc.smart_api.ltpData(
                exchange=instrument["exchange"],
                tradingsymbol=instrument["symbol"],
                symboltoken=instrument["token"]
            )
            if not raw or not raw.get("status"):
                if raw:
                    msg = str(raw.get("message", "")).lower()
                    err_code = raw.get("errorCode", "")
                    if "token missing" in msg or err_code == "AG8003" or "unauthorized" in msg:
                        svc.is_logged_in = False
                return False

            d = raw.get("data", {})
            ltp = float(d.get("ltp", 0))
            if ltp <= 0:
                return False

            pc = float(d.get("close", 0))
            change = round(ltp - pc, 2) if pc else 0
            change_pct = round(((ltp - pc) / pc) * 100, 2) if pc and pc != 0 else 0
            now_ts = time.time()

            new_tick = {
                "current_price": ltp,
                "current": ltp,
                "open": float(d.get("open", 0)),
                "high": float(d.get("high", 0)),
                "low": float(d.get("low", 0)),
                "prev_close": pc,
                "change": change,
                "change_pct": change_pct,
                "_ts": now_ts,
                "_received_ts": now_ts,
                "_source": "critical_poller",
                "time": now_ts,
            }

            # Only update & broadcast if the price actually changed
            with svc.latest_ticks_lock:
                existing = svc.latest_ticks.get(ticker, {})
                # ALWAYS prefer live WS tick if it arrived in last 2 seconds
                ws_recv_ts = existing.get("_received_ts", 0)
                if (
                    existing.get("_source") == "angel_ws"
                    and now_ts - ws_recv_ts < 2
                ):
                    return True  # WS is fresher

                prev_ltp = existing.get("current_price", 0)
                # Preserve OHLC from WS if REST has zeros
                if new_tick["open"] == 0 and existing.get("open", 0) > 0:
                    new_tick["open"] = existing["open"]
                if new_tick["high"] == 0 and existing.get("high", 0) > 0:
                    new_tick["high"] = existing["high"]
                if new_tick["low"] == 0 and existing.get("low", 0) > 0:
                    new_tick["low"] = existing["low"]
                if existing.get("volume", 0) > 0:
                    new_tick["volume"] = existing["volume"]

                svc.latest_ticks[ticker] = new_tick

            # Broadcast on every poll (ensures dashboard never shows stale price)
            if self.on_price_update:
                try:
                    self.on_price_update(ticker, new_tick)
                except Exception:
                    pass

            self._stats["updates"] += 1
            return True

        except Exception as e:
            self._stats["errors"] += 1
            return False

    def _run_loop(self):
        """Main polling loop — runs on dedicated daemon thread."""
        svc = self.service

        # Wait for login
        for _ in range(30):
            if self._stop_event.is_set():
                return
            if svc.is_logged_in and svc.smart_api:
                break
            time.sleep(1)
        else:
            print("[CriticalIndexPoller] AngelOne not logged in — thread exiting")
            return

        # Resolve all tokens before starting
        self._resolve_tokens()
        print(f"[CriticalIndexPoller] Tokens resolved: {list(self._token_cache.keys())}")

        while not self._stop_event.is_set():
            cycle_start = time.time()

            # Re-resolve tokens for any missing
            if len(self._token_cache) < len(CRITICAL_INDICES):
                self._resolve_tokens()

            # Poll each critical index sequentially (only 6, very fast)
            cycle_success = False
            for ticker in CRITICAL_INDICES:
                if self._stop_event.is_set():
                    break
                if self._poll_ticker(ticker):
                    cycle_success = True
                self._stats["polls"] += 1
                time.sleep(0.05)  # 50ms between each, so 6 indices = ~300ms total

            if cycle_success:
                self._consecutive_cycle_failures = 0
                self._current_interval = CRITICAL_POLL_INTERVAL
            else:
                self._consecutive_cycle_failures += 1
                if self._consecutive_cycle_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self._current_interval = min(
                        self._current_interval * self.BACKOFF_MULTIPLIER,
                        self.MAX_BACKOFF_INTERVAL
                    )
                    if self._consecutive_cycle_failures == self.MAX_CONSECUTIVE_FAILURES:
                        print(f"[CriticalIndexPoller] Circuit breaker: {self._consecutive_cycle_failures} "
                              f"consecutive failed cycles, backing off to {self._current_interval:.0f}s")

            elapsed = time.time() - cycle_start
            remaining = self._current_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def get_stats(self) -> dict:
        return dict(self._stats)
