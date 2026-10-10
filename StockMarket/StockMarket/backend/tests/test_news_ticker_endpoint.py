"""Phase 2 (B1/B3) regression: /api/news/ticker/{ticker}.

Two defects made this endpoint ALWAYS return {"articles": []}:

  * the live-quote branch required `last_traded_price` / `change_per`, which the
    normalized tick dict (angelone_service._handle_ws_tick) never contains -- so
    it never fired; and
  * the DB fallback read `row.date`, but models.Candle has no `date` column
    (only `timestamp`), so it raised AttributeError into the broad `except`.

The Overview page falls back to this endpoint when /api/news/search is cold, so
the always-empty result surfaced as "No recent news articles found for this
ticker".

Also (B3): an empty result used to be cached for the full 180s TTL, pinning
"no news"; it is now cached only briefly.

Runs against in-memory SQLite -- no production/external data is touched.
"""
import time
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
import main
from database import Base
from angelone_service import angelone_service as svc


def _mk_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


TICKER = "ZZNEWSTICKER"


class NewsTickerEndpointTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_session()
        main._news_ticker_cache.clear()
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)

    def tearDown(self):
        main._news_ticker_cache.clear()
        with svc.latest_ticks_lock:
            svc.latest_ticks.pop(TICKER, None)
        self.db.close()

    def _seed_1d(self, close=108.0, open_=100.0):
        self.db.add(models.Candle(
            ticker=TICKER, timeframe="1D",
            timestamp=datetime(2026, 3, 2, 15, 30),
            open=open_, high=110.0, low=95.0, close=close, volume=1000,
            is_completed=True,
        ))
        self.db.commit()

    def _set_live_tick(self, cp, pc):
        with svc.latest_ticks_lock:
            svc.latest_ticks[TICKER] = {
                "current_price": cp, "prev_close": pc, "open": pc,
                "high": max(cp, pc), "low": min(cp, pc), "volume": 5000,
                "_source": "angel_ws", "_exch_ts": time.time(),
            }

    # -- B1: DB fallback uses the real timestamp column --------------------
    def test_db_fallback_returns_an_article(self):
        self._seed_1d()
        res = main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(len(res["articles"]), 1)
        art = res["articles"][0]
        self.assertIn("gained", art["title"])
        self.assertEqual(art["source"], "Market Data")
        self.assertEqual(art["published_at"], "2026-03-02")

    def test_db_fallback_handles_a_down_day(self):
        self._seed_1d(open_=110.0, close=100.0)
        res = main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(len(res["articles"]), 1)
        self.assertIn("lost", res["articles"][0]["title"])
        self.assertEqual(res["articles"][0]["sentiment"], "negative")

    # -- B1: live branch uses the real normalized tick keys ----------------
    def test_live_tick_branch_uses_normalized_fields(self):
        self._set_live_tick(cp=108.0, pc=100.0)
        res = main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(len(res["articles"]), 1)
        self.assertEqual(res["articles"][0]["source"], "Live Market Data")
        self.assertIn("gained", res["articles"][0]["title"])

    def test_live_tick_takes_precedence_over_db(self):
        self._seed_1d(open_=200.0, close=210.0)   # DB would say +5%
        self._set_live_tick(cp=108.0, pc=100.0)   # live says +8%
        res = main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(res["articles"][0]["source"], "Live Market Data")
        self.assertIn("8.00", res["articles"][0]["title"])

    # -- contract: no live quote and no candle -> empty (not an error) -----
    def test_no_data_returns_empty_articles(self):
        res = main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(res, {"articles": []})

    # -- B3: empty result is NOT cached for the full TTL -------------------
    def test_empty_result_uses_short_ttl(self):
        main.proxy_news_ticker(TICKER, db=self.db)
        entry = main._news_ticker_cache.get(TICKER)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["ttl"], main._NEWS_TICKER_EMPTY_TTL)
        self.assertLess(entry["ttl"], main._NEWS_CACHE_TTL)

    def test_non_empty_result_uses_full_ttl(self):
        self._seed_1d()
        main.proxy_news_ticker(TICKER, db=self.db)
        entry = main._news_ticker_cache.get(TICKER)
        self.assertEqual(entry["ttl"], main._NEWS_CACHE_TTL)

    def test_empty_cache_expires_quickly_and_recovers(self):
        # first call: empty (no data) -> cached briefly
        main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(
            main._news_ticker_cache[TICKER]["ttl"], main._NEWS_TICKER_EMPTY_TTL)
        # simulate the short TTL elapsing
        main._news_ticker_cache[TICKER]["ts"] -= (main._NEWS_TICKER_EMPTY_TTL + 1)
        # data appears -> the very next call must see it (no 180s pin)
        self._seed_1d()
        res = main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(len(res["articles"]), 1)

    # -- cached non-empty result is served within its TTL ------------------
    def test_non_empty_result_is_served_from_cache(self):
        self._seed_1d()
        main.proxy_news_ticker(TICKER, db=self.db)
        # remove the underlying row: the cached response must still be returned
        self.db.query(models.Candle).filter(models.Candle.ticker == TICKER).delete()
        self.db.commit()
        res = main.proxy_news_ticker(TICKER, db=self.db)
        self.assertEqual(len(res["articles"]), 1)

    # -- the invalid candle attribute is gone from the source --------------
    def test_source_no_longer_reads_invalid_candle_date(self):
        import inspect
        src = inspect.getsource(main.proxy_news_ticker)
        self.assertNotIn("row.date", src)
        self.assertIn("row.timestamp.date()", src)

    # -- provider / derivation failure degrades cleanly, not silently -------
    def test_derivation_failure_returns_empty_contract(self):
        class _BrokenSession:
            def query(self, *a, **k):
                raise RuntimeError("db exploded")
        # must NOT raise (the endpoint keeps its {"articles": [...]} contract)
        res = main.proxy_news_ticker(TICKER, db=_BrokenSession())
        self.assertEqual(res, {"articles": []})
        # ...and the failure result is cached only briefly, not for 180s
        entry = main._news_ticker_cache.get(TICKER)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["ttl"], main._NEWS_TICKER_EMPTY_TTL)

    def test_derivation_failure_is_logged_not_silent(self):
        import inspect
        src = inspect.getsource(main.proxy_news_ticker)
        # a programming/DB error must surface in the logs, not vanish
        self.assertIn("logging.getLogger(__name__).exception", src)


if __name__ == "__main__":
    unittest.main()
