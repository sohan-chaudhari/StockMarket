"""Regression tests for two reported production bugs.

BUG A - dashboard kept moving while the market was closed.
    The backend still relays the broker's last snapshot after the session ends,
    and dashboard.js applied EVERY websocket `price_update` to the ticker strip,
    the minichart and the Top Gainers/Losers rows. The REST fallback poller
    already guarded on `window._marketOpen === false`; the websocket path did
    not, so a closed market still appeared to tick.

BUG B - overview.html showed no news while news.html did.
    /api/news/search/{query} is serve-stale + background-refresh: on a COLD
    cache it returned [] immediately and refreshed in the background. A page
    that queries once (overview) fell back to the synthetic quote article,
    while a page warmed by repeated queries (news.html) worked. It now waits a
    bounded time for the in-flight refresh.

BUG C - /api/news/general always failed with
    UnboundLocalError: local variable 'asyncio' referenced before assignment,
    because a function-local `import asyncio` shadowed the module import.
"""
import asyncio
import os
import re
import unittest
from unittest.mock import patch

import main

FRONTEND_DASHBOARD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "dashboard.js")

TITLE = "Testco Industries reports strong Q2 profit growth of 18 percent"


class _FakeEntry(dict):
    def __getattr__(self, k):
        return self.get(k)


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


class _FakeResp:
    text = "<rss/>"


class NewsColdMissTests(unittest.TestCase):
    """A cold /api/news/search must return real articles, not []."""

    def setUp(self):
        main._news_search_cache.clear()
        main._news_inflight_tasks.clear()
        self.addCleanup(main._news_search_cache.clear)
        self.addCleanup(main._news_inflight_tasks.clear)

    def _entry(self):
        return _FakeEntry(
            title=TITLE,
            link="https://scanx.trade/testco-q2-results",
            published="Sat, 11 Oct 2026 06:00:00 GMT",
            description="Testco Industries reported an 18 percent rise in profit.",
        )

    def test_cold_miss_returns_articles_after_bounded_wait(self):
        with patch("requests.get", lambda *a, **k: _FakeResp()), \
             patch("feedparser.parse", lambda text: _FakeFeed([self._entry()])), \
             patch.object(main, "_resolve_company_name", lambda q: (None, None)):
            out = asyncio.run(main.news_search("TESTCO", 20))
        self.assertIsInstance(out, list)
        self.assertTrue(out, "a cold miss must return the refreshed articles, not []")
        self.assertIn("testco", out[0]["title"].lower())

    def test_warm_cache_is_served_without_refetch(self):
        main._news_search_cache["testco:20"] = {
            "data": [{"title": TITLE, "url": "https://scanx.trade/x"}],
            "ts": main.time.time(),
        }
        with patch("requests.get", side_effect=AssertionError("must not refetch")):
            out = asyncio.run(main.news_search("TESTCO", 20))
        self.assertEqual(len(out), 1)

    def test_cold_wait_is_bounded(self):
        self.assertGreater(main._NEWS_COLD_WAIT_SEC, 0)
        self.assertLessEqual(main._NEWS_COLD_WAIT_SEC, 15)


class NewsGeneralAsyncioShadowTests(unittest.TestCase):
    """A local `import asyncio` made the whole function treat asyncio as local."""

    def test_proxy_news_general_has_no_local_asyncio_binding(self):
        varnames = main.proxy_news_general.__code__.co_varnames
        self.assertNotIn(
            "asyncio", varnames,
            "proxy_news_general must not bind a local `asyncio` (it shadows the "
            "module import and breaks the earlier await asyncio.to_thread(...))")


class DashboardClosedMarketGuardTests(unittest.TestCase):
    """onPriceUpdate must not apply websocket prices while the market is closed."""

    def _on_price_update_body(self):
        with open(FRONTEND_DASHBOARD, "r", encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"function onPriceUpdate\(evt\)\s*\{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(m, "onPriceUpdate not found")
        return m.group(1)

    def test_guard_is_the_first_statement(self):
        body = self._on_price_update_body()
        head = body.strip().splitlines()[0:8]
        head_txt = "\n".join(head)
        self.assertIn("window._marketOpen === false", head_txt)
        self.assertRegex(head_txt, r"if\s*\(window\._marketOpen === false\)\s*return")

    def test_guard_precedes_any_price_write(self):
        body = self._on_price_update_body()
        gi = body.find("window._marketOpen === false")
        for later in ("updateTickerStrip", "updateNiftyLiveCandle", "pEl.innerText"):
            li = body.find(later)
            if li != -1:
                self.assertLess(gi, li, "guard must come before %s" % later)


if __name__ == "__main__":
    unittest.main()
