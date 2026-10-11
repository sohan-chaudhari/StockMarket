"""Regression tests for MULTI-WORD free-text news search.

Root cause: for a multi-word query that did not resolve to a platform stock
(e.g. "groww billionbrains", "billion brains"), `news_search` built the
relevance filter from the query as ONE literal phrase. Articles that mentioned
the words separately -- the normal case -- therefore never matched and the
search always returned nothing, even though the Google-News query itself
returned plenty of ScanX articles.

Fix: an unresolved multi-word query is treated as a free-text brand/company
search -- the RSS queries include the distinctive tokens and the relevance
filter matches any of those tokens (mirroring how a resolved company's words
are already matched).

These tests are fully deterministic: `_FUZZY_NAME_INDEX` is seeded and the news
provider (requests.get / feedparser.parse) is mocked -- no live provider is
contacted.
"""
import asyncio
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

import main

# Deliberately contains NO "groww"/"billionbrains" entry, so the query stays
# unresolved and exercises the free-text path.
_SEED_INDEX = [
    ("reliance industries limited", "RELIANCE", "Reliance Industries Limited"),
    ("state bank of india", "SBIN", "State Bank of India"),
    ("chennai petroleum corporation limited", "CHENNPETRO",
     "Chennai Petroleum Corporation Limited"),
]


class _FakeResp:
    def __init__(self, text=""):
        self.text = text
        self.status_code = 200


def _entry(title, link, desc):
    return SimpleNamespace(
        title=title,
        link=link,
        published="Wed, 08 Oct 2026 10:00:00 GMT",
        description=desc,
        published_parsed=None,
        source={"title": "scanx.trade"},
    )


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


class MultiWordNewsSearchTests(unittest.TestCase):
    def setUp(self):
        self._orig = main._FUZZY_NAME_INDEX
        main._FUZZY_NAME_INDEX = list(_SEED_INDEX)
        main._news_search_cache.clear()
        main._news_inflight_tasks.clear()
        self.urls = []

    def tearDown(self):
        main._news_search_cache.clear()
        main._news_inflight_tasks.clear()
        main._FUZZY_NAME_INDEX = self._orig

    def _fake_get(self, url, *a, **k):
        self.urls.append(url)
        return _FakeResp("<rss/>")

    def _decoded(self):
        """Recorded provider URLs, percent-decoded, as one lowercased string."""
        return urllib.parse.unquote_plus(" ".join(self.urls)).lower()

    def _run(self, query, entries):
        async def _go():
            r1 = await main.news_search(query, 20)
            inflight = list(main._news_inflight_tasks.values())
            if inflight:
                await asyncio.gather(*inflight, return_exceptions=True)
            r2 = await main.news_search(query, 20)
            return r1, r2

        with patch("requests.get", side_effect=self._fake_get), \
             patch("feedparser.parse",
                   side_effect=lambda *a, **k: _FakeFeed(entries)):
            return asyncio.run(_go())

    # An article that mentions the two words SEPARATELY (never contiguously) --
    # exactly the shape the old phrase filter rejected.
    _SPLIT = _entry(
        "Peak XV Partners sells 16.69 crore Groww shares as Billionbrains stake falls",
        "https://www.scanx.trade/groww-billionbrains-stake",
        "Billionbrains Garage Ventures, the parent of Groww, saw a stake sale.",
    )

    def test_split_word_article_is_found(self):
        r1, r2 = self._run("groww billionbrains", [self._SPLIT])
        # Cold-cache contract CHANGED (deliberately): the first request for a
        # query now waits briefly for the in-flight refresh instead of returning
        # [] immediately. Returning [] was what made overview.html show "no
        # news" while news.html (warmed by progressive typing) worked.
        self.assertTrue(r1, "a cold miss must now return the refreshed articles")
        self.assertTrue(r2, "multi-word query must find a split-word article")
        self.assertIn("Groww", r2[0]["title"])

    def test_distinctive_tokens_reach_the_provider(self):
        self._run("groww billionbrains", [self._SPLIT])
        joined = self._decoded()
        self.assertIn("groww", joined)
        self.assertIn("billionbrains", joined)

    def test_spaced_variant_is_found(self):
        _r1, r2 = self._run("billion brains", [self._SPLIT])
        self.assertTrue(r2)

    def test_irrelevant_article_is_still_rejected(self):
        other = _entry(
            "Tata Steel reports record quarterly production",
            "https://www.scanx.trade/tata-steel",
            "Tata Steel announced record output at its plant.",
        )
        _r1, r2 = self._run("groww billionbrains", [other])
        self.assertEqual(r2, [], "an article with neither token must be rejected")

    def test_resolved_company_query_is_unchanged(self):
        canon = _entry(
            "Chennai Petroleum Corporation reports strong quarterly results",
            "https://www.scanx.trade/chennai-petroleum-q2",
            "Chennai Petroleum Corporation Limited announced results today.",
        )
        _r1, r2 = self._run("chennai petrolium corporation", [canon])
        self.assertTrue(r2)
        joined = self._decoded()
        self.assertIn("chennpetro", joined)  # still resolves to the ticker
        self.assertNotIn("groww", joined)

    def test_single_word_query_is_unchanged(self):
        art = _entry(
            "Groww share price slips 4 percent on huge volumes",
            "https://www.scanx.trade/groww",
            "Groww shares fell sharply in early trade.",
        )
        _r1, r2 = self._run("groww", [art])
        self.assertTrue(r2)


if __name__ == "__main__":
    unittest.main()
