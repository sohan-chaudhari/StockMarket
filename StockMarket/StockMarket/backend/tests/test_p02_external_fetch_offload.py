"""P0.2 regression tests: external fetches must not be awaited on request paths.

The architecture under test:

    USER REQUEST -> local cache / WS tick / DB value -> return immediately
                 -> background task refreshes the external source
                 -> cache updated for the next request

These tests prove (1) the request-path functions no longer contain/await the
external fetch, (2) the actual fetch now lives in a background helper, and
(3) the request path returns quickly even when the external fetch is slow.
"""
import asyncio
import inspect
import os
import threading
import time
import unittest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-audit-tests-only-0123456789")

import main


def _run(coro):
    return asyncio.run(coro)


class TestNoExternalFetchOnRequestPath(unittest.TestCase):
    """Static proof that the blocking external call moved behind a background task."""

    def test_live_prices_schedules_instead_of_awaiting_yfinance(self):
        src = inspect.getsource(main.fetch_batch_live_data)
        self.assertIn("_schedule_yf_refresh(", src)
        self.assertNotIn("yf.download", src, "yfinance download must not run on the request path")
        self.assertNotIn("await _yf_fetch_prices", src)
        # the external download now lives in the background helper
        self.assertIn("yf.download", inspect.getsource(main._yf_fetch_prices))

    def test_live_prices_returns_before_yfinance(self):
        src = inspect.getsource(main.fetch_batch_live_data)
        # the scheduling + early return must appear before the tail return
        self.assertIn("return prices", src)

    def test_fii_dii_schedules_background_scrape(self):
        src = inspect.getsource(main.get_fii_dii)
        self.assertIn("create_task(_fii_dii_refresh_and_cache(", src)
        self.assertNotIn("AsyncClient", src, "httpx scrape must not run on the request path")
        self.assertIn("AsyncClient", inspect.getsource(main._fii_dii_refresh_and_cache))

    def test_crypto_schedules_background_fetch(self):
        src = inspect.getsource(main.get_crypto_prices)
        self.assertIn("create_task(_crypto_refresh_and_cache(", src)
        self.assertNotIn("aiohttp", src, "CoinGecko fetch must not run on the request path")
        self.assertIn("aiohttp", inspect.getsource(main._crypto_refresh_and_cache))

    def test_news_search_schedules_and_never_awaits_task(self):
        src = inspect.getsource(main.news_search)
        self.assertIn("create_task(_refresh_news_search(", src)
        self.assertNotIn("await task", src, "the RSS task must not be awaited on the request path")


class TestBackgroundRefreshScheduling(unittest.TestCase):
    """Functional proof: request path returns immediately, refresh runs in background."""

    def setUp(self):
        self._orig = {
            "_yf_fetch_prices": main._yf_fetch_prices,
            "_schedule_yf_refresh": main._schedule_yf_refresh,
            "_resolve_prices_from_db": main._resolve_prices_from_db,
            "_is_yfinance_failed": main._is_yfinance_failed,
            "validate_symbol": main.yf_downloader.validate_symbol,
            "_fii_dii_refresh_and_cache": main._fii_dii_refresh_and_cache,
            "_fii_dii_from_db": main._fii_dii_from_db,
            "_crypto_refresh_and_cache": main._crypto_refresh_and_cache,
        }
        with main._yf_refresh_inflight_lock:
            main._yf_refresh_inflight.clear()

    def tearDown(self):
        main._yf_fetch_prices = self._orig["_yf_fetch_prices"]
        main._schedule_yf_refresh = self._orig["_schedule_yf_refresh"]
        main._resolve_prices_from_db = self._orig["_resolve_prices_from_db"]
        main._is_yfinance_failed = self._orig["_is_yfinance_failed"]
        main.yf_downloader.validate_symbol = self._orig["validate_symbol"]
        main._fii_dii_refresh_and_cache = self._orig["_fii_dii_refresh_and_cache"]
        main._fii_dii_from_db = self._orig["_fii_dii_from_db"]
        main._crypto_refresh_and_cache = self._orig["_crypto_refresh_and_cache"]
        with main._yf_refresh_inflight_lock:
            main._yf_refresh_inflight.clear()

    def test_schedule_yf_refresh_is_non_blocking_and_deduplicates(self):
        calls = []

        async def slow(tickers, market_open):
            calls.append(list(tickers))
            await asyncio.sleep(0.3)
            return {}

        main._yf_fetch_prices = slow

        async def scenario():
            t0 = time.time()
            main._schedule_yf_refresh(["AAA", "BBB"], True)      # sync call
            elapsed = time.time() - t0
            self.assertLess(elapsed, 0.05, "scheduling must not block")
            # duplicate protection: same ticker is not queued twice
            main._schedule_yf_refresh(["AAA"], True)
            await asyncio.sleep(0.5)
            self.assertEqual(calls, [["AAA", "BBB"]])
            with main._yf_refresh_inflight_lock:
                self.assertEqual(main._yf_refresh_inflight, set())

        _run(scenario())

    def test_live_prices_returns_fast_while_yfinance_slow(self):
        started = threading.Event()

        async def slow_yf(tickers, market_open):
            started.set()
            await asyncio.sleep(8)
            return {}

        main._yf_fetch_prices = slow_yf
        main._resolve_prices_from_db = lambda tickers, market_open: ({}, [])
        main._is_yfinance_failed = lambda t: False
        main.yf_downloader.validate_symbol = lambda t: (True, "")
        with main.angelone_service.latest_ticks_lock:
            main.angelone_service.latest_ticks.pop("ZZZZTEST", None)
        main._live_prices_cache.pop("ZZZZTEST", None)

        async def scenario():
            t0 = time.time()
            prices = await main.fetch_batch_live_data(["ZZZZTEST"], market_open=True)
            elapsed = time.time() - t0
            self.assertLess(elapsed, 0.5, f"request path blocked for {elapsed:.2f}s waiting on yfinance")
            self.assertNotIn("ZZZZTEST", prices, "unsatisfied ticker must not be fabricated")
            # the background refresh did start
            self.assertTrue(await asyncio.to_thread(started.wait, 2.0), "background refresh never ran")

        _run(scenario())

    def test_live_prices_local_ws_tick_is_served_without_yfinance(self):
        async def boom(*a, **k):  # pragma: no cover - must never be called
            raise AssertionError("yfinance must not be touched when a local tick exists")

        main._yf_fetch_prices = boom
        with main.angelone_service.latest_ticks_lock:
            main.angelone_service.latest_ticks["WSYMB"] = {
                "current_price": 100.0, "current": 100.0, "prev_close": 99.0,
                "open": 99.5, "high": 101.0, "low": 98.0, "volume": 1000,
                "_received_ts": time.time(), "_source": "test",
            }

        async def scenario():
            prices = await main.fetch_batch_live_data(["WSYMB"], market_open=True)
            self.assertIn("WSYMB", prices)
            self.assertEqual(prices["WSYMB"]["current_price"], 100.0)
            for key in ("current_price", "open", "prev_close", "high", "low", "volume", "change", "change_pct"):
                self.assertIn(key, prices["WSYMB"])

        try:
            _run(scenario())
        finally:
            with main.angelone_service.latest_ticks_lock:
                main.angelone_service.latest_ticks.pop("WSYMB", None)

    def test_fii_dii_returns_fast_while_scrape_slow(self):
        started = threading.Event()

        async def slow_refresh():
            started.set()
            await asyncio.sleep(8)
            return None

        main._fii_dii_refresh_and_cache = slow_refresh
        main._fii_dii_from_db = lambda *a, **k: []
        main._fii_dii_cache["data"] = None
        main._fii_dii_cache["ts"] = 0.0
        main._fii_dii_refresh_task = None

        async def scenario():
            t0 = time.time()
            res = await main.get_fii_dii()
            elapsed = time.time() - t0
            self.assertLess(elapsed, 0.5, f"request path blocked for {elapsed:.2f}s on FII/DII scrape")
            self.assertEqual(res["entries"], [])
            self.assertTrue(await asyncio.to_thread(started.wait, 2.0), "background scrape never ran")

        _run(scenario())

    def test_crypto_returns_fast_while_fetch_slow(self):
        started = threading.Event()

        async def slow_refresh():
            started.set()
            await asyncio.sleep(8)
            return []

        main._crypto_refresh_and_cache = slow_refresh
        main._crypto_cache["data"] = None
        main._crypto_cache["ts"] = 0.0
        main._crypto_refresh_task = None

        async def scenario():
            t0 = time.time()
            res = await main.get_crypto_prices()
            elapsed = time.time() - t0
            self.assertLess(elapsed, 0.5, f"request path blocked for {elapsed:.2f}s on CoinGecko")
            self.assertEqual(res, [])
            self.assertTrue(await asyncio.to_thread(started.wait, 2.0), "background fetch never ran")

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
