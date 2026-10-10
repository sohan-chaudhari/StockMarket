"""Regression tests for typo-tolerant news search (company-name resolution).

Root cause: `news_search` treated a free-text query as an EXACT phrase -- both
the Google-News queries and the relevance filter were built from the literal
text. A misspelling such as "chennai petrolium corporation" therefore queried
the misspelled phrase AND required it to appear in every article, so real
"Chennai Petroleum Corporation" articles were all discarded and the user saw
"No news found".

Fix: when a query is not a known ticker, it is fuzzy-matched (difflib) against
the platform's stock names; on a confident match the canonical ticker + name
are used for query construction, relevance filtering and the fallback.

These tests are fully deterministic: `_FUZZY_NAME_INDEX` is seeded directly and
the news provider (requests.get / feedparser.parse) is mocked -- no live Google
News or other external provider is contacted.
"""
import asyncio
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

import main

CANON = ("CHENNPETRO", "Chennai Petroleum Corporation Limited")

_SEED_INDEX = [
    ("chennai petroleum corporation limited", "CHENNPETRO", "Chennai Petroleum Corporation Limited"),
    ("reliance industries limited", "RELIANCE", "Reliance Industries Limited"),
    ("state bank of india", "SBIN", "State Bank of India"),
    ("tata consultancy services limited", "TCS", "Tata Consultancy Services Limited"),
    ("infosys limited", "INFY", "Infosys Limited"),
    ("south indian bank limited", "SOUTHBANK", "South Indian Bank Limited"),
    ("india cements limited", "INDIACEM", "India Cements Limited"),
    ("hdfc bank limited", "HDFCBANK", "HDFC Bank Limited"),
]


class _SeededIndex(unittest.TestCase):
    def setUp(self):
        self._orig = main._FUZZY_NAME_INDEX
        main._FUZZY_NAME_INDEX = list(_SEED_INDEX)

    def tearDown(self):
        main._FUZZY_NAME_INDEX = self._orig


class NormalizeNameTests(_SeededIndex):
    def test_case_punctuation_ampersand_whitespace(self):
        self.assertEqual(main._normalize_stock_name("Chennai Petroleum Corp. Ltd."),
                         "chennai petroleum corp ltd")
        self.assertEqual(main._normalize_stock_name("M&M"), "m and m")
        self.assertEqual(main._normalize_stock_name("  Tata   Motors  "), "tata motors")
        self.assertEqual(main._normalize_stock_name(None), "")
        self.assertEqual(main._normalize_stock_name(""), "")


class CompanyNameResolutionTests(_SeededIndex):
    def _res(self, q):
        return main._resolve_company_name(q)

    def test_typo_resolves_to_canonical_company(self):
        self.assertEqual(self._res("chennai petrolium corporation"), CANON)

    def test_typo_uppercase_resolves(self):
        self.assertEqual(self._res("CHENNAI PETROLIUM CORPORATION"), CANON)

    def test_typo_with_extra_whitespace_and_punctuation(self):
        self.assertEqual(self._res("  chennai,  petrolium   corporation!! "), CANON)

    def test_correct_spelling_still_resolves(self):
        self.assertEqual(self._res("Chennai Petroleum Corporation"), CANON)

    def test_full_correct_name_resolves(self):
        self.assertEqual(self._res("Chennai Petroleum Corporation Limited"), CANON)

    def test_other_company_names_resolve(self):
        self.assertEqual(self._res("reliance industries")[0], "RELIANCE")
        self.assertEqual(self._res("state bank of india")[0], "SBIN")
        self.assertEqual(self._res("hdfc bank")[0], "HDFCBANK")

    def test_general_multiworld_queries_are_not_matched(self):
        for q in ["market news today", "top gainers and losers",
                  "budget 2026 highlights", "ipo listing this week",
                  "quarterly results season"]:
            self.assertEqual(self._res(q), (None, None), q)

    def test_short_or_single_token_queries_are_not_matched(self):
        for q in ["bank", "chennai", "tcs", "news", "india", "reliance"]:
            self.assertEqual(self._res(q), (None, None), q)

    def test_unmatched_multiworld_query(self):
        self.assertEqual(self._res("zzyzx qwerty corporation"), (None, None))

    def test_empty_and_whitespace(self):
        self.assertEqual(self._res(""), (None, None))
        self.assertEqual(self._res("     "), (None, None))

    def test_no_index_is_safe(self):
        main._FUZZY_NAME_INDEX = []
        self.assertEqual(main._resolve_company_name("chennai petrolium corporation"), (None, None))


# ── End-to-end endpoint tests (provider mocked) ────────────────────────────

class _FakeResp:
    def __init__(self, text=""):
        self.text = text
        self.status_code = 200


def _entry(title, link):
    return SimpleNamespace(
        title=title,
        link=link,
        published="Wed, 08 Oct 2026 10:00:00 GMT",
        description="Chennai Petroleum Corporation Limited announced results today.",
        published_parsed=None,
        source={"title": "scanx.trade"},
    )


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


class NewsSearchEndToEndTests(_SeededIndex):
    """Drives the real async endpoint with the provider mocked; asserts the
    CANONICAL terms (not the misspelled input) reach the provider and the
    relevance filter."""

    def setUp(self):
        super().setUp()
        main._news_search_cache.clear()
        main._news_inflight_tasks.clear()
        self.urls = []
        # an article that mentions the canonical name but NOT the misspelling
        self._entry = _entry(
            "Chennai Petroleum Corporation reports strong quarterly results",
            "https://www.scanx.trade/chennai-petroleum-q2",
        )

    def tearDown(self):
        main._news_search_cache.clear()
        main._news_inflight_tasks.clear()
        super().tearDown()

    def _fake_get(self, url, *a, **k):
        self.urls.append(url)
        return _FakeResp("<rss/>")

    def _decoded(self):
        """Recorded provider URLs, percent-decoded, as one lowercased string."""
        return urllib.parse.unquote_plus(" ".join(self.urls)).lower()

    def _run(self, query):
        async def _go():
            r1 = await main.news_search(query, 20)
            inflight = list(main._news_inflight_tasks.values())
            if inflight:
                await asyncio.gather(*inflight, return_exceptions=True)
            r2 = await main.news_search(query, 20)
            return r1, r2

        with patch("requests.get", side_effect=self._fake_get), \
             patch("feedparser.parse", side_effect=lambda *a, **k: _FakeFeed([self._entry])):
            return asyncio.run(_go())

    def test_typo_query_uses_canonical_terms(self):
        r1, r2 = self._run("chennai petrolium corporation")
        # cold-cache contract unchanged: first response is an empty list
        self.assertEqual(r1, [])
        joined = self._decoded()
        self.assertIn("chennpetro", joined, "canonical ticker must reach the provider")
        self.assertIn("chennai petroleum", joined,
                      "canonical company name must reach the provider")
        # the canonical-name article must pass the relevance filter
        self.assertTrue(r2, "news should be found for the typo query")
        self.assertIn("Chennai Petroleum Corporation", r2[0]["title"])

    def test_ticker_query_is_unchanged(self):
        r1, r2 = self._run("CHENNPETRO")
        self.assertEqual(r1, [])
        self.assertIn("chennpetro", self._decoded())

    def test_reliance_ticker_is_unchanged(self):
        _r1, _r2 = self._run("RELIANCE")
        joined = self._decoded()
        self.assertIn("reliance", joined)
        self.assertNotIn("chennpetro", joined)

    def test_general_query_is_not_remapped(self):
        _r1, _r2 = self._run("market news today")
        joined = self._decoded()
        self.assertIn("market news today", joined)
        self.assertNotIn("chennpetro", joined)

    def test_primary_and_fallback_use_canonical_terms(self):
        # force the primary pass to yield nothing (empty feed), so the broader
        # fallback runs; it must still use the canonical terms.
        with patch("requests.get", side_effect=self._fake_get), \
             patch("feedparser.parse", side_effect=lambda *a, **k: _FakeFeed([])):
            async def _go():
                await main.news_search("chennai petrolium corporation", 20)
                inflight = list(main._news_inflight_tasks.values())
                if inflight:
                    await asyncio.gather(*inflight, return_exceptions=True)
                return await main.news_search("chennai petrolium corporation", 20)
            asyncio.run(_go())
        joined = self._decoded()
        # the fallback query is "<cname> stock news", with the canonical name
        self.assertIn("chennai petroleum stock news", joined)


if __name__ == "__main__":
    unittest.main()
