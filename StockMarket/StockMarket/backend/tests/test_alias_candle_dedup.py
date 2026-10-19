"""Alias-spelling deduplication regression tests.

candles() legitimately stores the same trading day / intraday bucket under more
than one ticker spelling:

  * TCS and TCS.NS            (the yfinance-style spelling)
  * NIFTY and NIFTY50         (an index alias; INDEX_ALIASES_MAP)

`_normalize_ticker()` fans the query out over every spelling on purpose, so a
ticker is found whichever one its rows were written under. Before this fix the
payload therefore carried one bar per ROW, i.e. two bars for the same timestamp
with different OHLCV:

  TCS      2026-02-23 close 2626.0986328125   |  TCS.NS  close 2676.3
  NIFTY    2026-10-16 close 23080.35          |  NIFTY50 close 23083.2

...and the duplicates consumed the page: /api/stock-data/range returned 500 rows
covering only 442 distinct days for TCS, silently dropping the oldest ~58.

Driven against in-memory SQLite. Every call uses `before=` (or a ticker whose
stored history already reaches the target trading day) so no gap-fill path can
reach a provider; no network is touched.
"""
import datetime
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import models

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _recent_weekdays(count):
    """`count` weekdays ending today (so the daily gap-fill needs nothing)."""
    out = []
    d = datetime.date.today()
    while len(out) < count:
        if d.weekday() < 5:
            out.append(datetime.datetime(d.year, d.month, d.day))
        d -= datetime.timedelta(days=1)
    return sorted(out)


def _route(fn):
    """Reach a route body without slowapi's limiter wrapper.

    The limiter needs a real ASGI Request and is not what these tests exercise --
    the payload logic is what changed. functools.wraps is preserved by slowapi, so
    __wrapped__ is the original function.
    """
    return getattr(fn, "__wrapped__", fn)


class _AliasFixture(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        models.StockMetadata.__table__.create(self.engine)
        # _stock_data_range_core also reads today's live candle; the table exists in
        # production, so create it here (empty) rather than tripping the query.
        models.CurrentDayCandle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def db(self):
        s = self.Session()
        self.addCleanup(s.close)
        return s

    def seed(self, rows):
        """rows: (ticker, timeframe, timestamp, open, high, low, close, volume)"""
        db = self.Session()
        try:
            for r in rows:
                db.add(models.Candle(ticker=r[0], timeframe=r[1], timestamp=r[2],
                                     open=r[3], high=r[4], low=r[5], close=r[6], volume=r[7]))
            db.commit()
        finally:
            db.close()


class DailyAliasDedupeTests(_AliasFixture):
    def test_tcs_and_tcs_ns_collapse_to_one_bar_and_canonical_wins(self):
        days = _recent_weekdays(20)
        rows = []
        for i, d in enumerate(days):
            rows.append(("TCS", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
            rows.append(("TCS.NS", "1D", d, 200 + i, 201 + i, 199 + i, 200.5 + i, 2000))
        self.seed(rows)

        out = main._stock_data_range_core("TCS", "ALL", self.db())
        self.assertEqual(len(out), 20, "one bar per trading day")
        self.assertEqual(len({r["time"] for r in out}), 20, "no repeated date")
        self.assertEqual(out[0]["close"], 100.5, "the canonical TCS row wins")
        self.assertNotEqual(out[0]["close"], 200.5, "never the TCS.NS decoy")

    def test_index_alias_prefers_the_spelling_the_user_asked_for(self):
        days = _recent_weekdays(15)
        rows = []
        for i, d in enumerate(days):
            rows.append(("NIFTY", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
            rows.append(("NIFTY50", "1D", d, 300 + i, 301 + i, 299 + i, 300.5 + i, 3000))
        self.seed(rows)

        as_nifty = main._stock_data_range_core("NIFTY", "ALL", self.db())
        as_nifty50 = main._stock_data_range_core("NIFTY50", "ALL", self.db())
        self.assertEqual(len(as_nifty), 15)
        self.assertEqual(len(as_nifty50), 15)
        self.assertEqual(as_nifty[0]["close"], 100.5, "NIFTY is canonical for a NIFTY request")
        self.assertEqual(as_nifty50[0]["close"], 300.5, "NIFTY50 is canonical for a NIFTY50 request")

    def test_alias_only_timestamps_are_preserved(self):
        days = _recent_weekdays(12)
        rows = []
        for d in days[:10]:                      # both spellings
            rows.append(("TCS", "1D", d, 100, 101, 99, 100.5, 1000))
            rows.append(("TCS.NS", "1D", d, 200, 201, 199, 200.5, 2000))
        for d in days[10:]:                      # ONLY under the alias
            rows.append(("TCS.NS", "1D", d, 250, 251, 249, 250.5, 2500))
        self.seed(rows)

        out = main._stock_data_range_core("TCS", "ALL", self.db())
        self.assertEqual(len(out), 12, "alias-only days must still be served")
        by_date = {r["time"]: r for r in out}
        for d in days[10:]:
            self.assertIn(str(d.date()), by_date)
            self.assertEqual(by_date[str(d.date())]["close"], 250.5,
                             "an alias-only day keeps the alias row's OHLC")

    def test_dedupe_is_chronological_and_deterministic(self):
        days = _recent_weekdays(12)
        rows = []
        for i, d in enumerate(days):
            rows.append(("TCS.NS", "1D", d, 200 + i, 201 + i, 199 + i, 200.5 + i, 2000))  # alias first
            rows.append(("TCS", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
        self.seed(rows)

        first = json.dumps(main._stock_data_range_core("TCS", "ALL", self.db()), sort_keys=True)
        second = json.dumps(main._stock_data_range_core("TCS", "ALL", self.db()), sort_keys=True)
        self.assertEqual(first, second, "repeated calls must be byte-identical")
        out = json.loads(first)
        times = [r["time"] for r in out]
        self.assertEqual(times, sorted(times), "oldest first (unchanged ordering)")
        self.assertEqual(out[0]["close"], 100.5,
                         "insertion order must not decide the winner")


class RangePaginationTests(_AliasFixture):
    def test_page_fills_with_distinct_days_and_crosses_boundaries_cleanly(self):
        days = _recent_weekdays(20)
        rows = []
        for i, d in enumerate(days):
            rows.append(("TCS", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
            rows.append(("TCS.NS", "1D", d, 200 + i, 201 + i, 199 + i, 200.5 + i, 2000))
        self.seed(rows)

        p1 = _route(main.get_range_paginated)(None, ticker="TCS", range="ALL", before=None, after=None, limit=6, db=self.db())
        self.assertEqual(len(p1["data"]), 6, "a 6-day page must contain 6 DISTINCT days")
        self.assertEqual(len({x["time"] for x in p1["data"]}), 6)
        self.assertTrue(p1["has_more"])

        oldest = p1["data"][0]["time"]
        p2 = _route(main.get_range_paginated)(None, ticker="TCS", range="ALL", before=oldest, after=None, limit=6, db=self.db())
        self.assertEqual(len(p2["data"]), 6)
        self.assertEqual(len({x["time"] for x in p2["data"]}), 6)
        self.assertTrue(set(x["time"] for x in p1["data"]).isdisjoint(x["time"] for x in p2["data"]),
                        "pages must not overlap when duplicates straddle the boundary")

    def test_has_more_counts_distinct_days_not_rows(self):
        days = _recent_weekdays(5)
        rows = []
        for i, d in enumerate(days):
            rows.append(("TCS", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
            rows.append(("TCS.NS", "1D", d, 200 + i, 201 + i, 199 + i, 200.5 + i, 2000))
        self.seed(rows)

        res = _route(main.get_range_paginated)(None, ticker="TCS", range="ALL", before=None, after=None, limit=5, db=self.db())
        self.assertEqual(len(res["data"]), 5)
        self.assertFalse(res["has_more"],
                         "5 distinct days with a limit of 5 is the last page, even though 10 ROWS exist")

    def test_single_spelling_pagination_is_unchanged(self):
        days = _recent_weekdays(12)
        self.seed([("TCS", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i, d in enumerate(days)])
        res = _route(main.get_range_paginated)(None, ticker="TCS", range="ALL", before=None, after=None, limit=5, db=self.db())
        self.assertEqual(len(res["data"]), 5)
        self.assertTrue(res["has_more"])
        self.assertEqual(res["data"][0]["time"], str(days[7].date()))


class IntradayAliasDedupeTests(_AliasFixture):
    def _seed_5m(self, tickers, buckets=6):
        base = datetime.datetime(2026, 10, 6, 9, 15)
        rows = []
        for i in range(buckets):
            ts = base + datetime.timedelta(minutes=5 * i)
            for name, tag in tickers:
                rows.append((name, "5m", ts,
                             100 + i + tag, 102 + i + tag, 99 + i + tag, 101 + i + tag,
                             1000 + i * 10 + tag * 9000))
        self.seed(rows)
        return base, buckets

    def _before_after_the_window(self, base, buckets):
        end = base + datetime.timedelta(minutes=5 * buckets + 5)
        return int(end.replace(tzinfo=IST).timestamp())

    def test_five_minute_buckets_collapse_and_keep_canonical_ohlcv(self):
        base, buckets = self._seed_5m([("HDFCBANK", 0), ("HDFCBANK.NS", 100)])
        res = main.get_intraday_paginated(
            ticker="HDFCBANK", interval="5m", before=self._before_after_the_window(base, buckets),
            after=None, limit=600, background_tasks=None, db=self.db())

        data = res["data"]
        self.assertEqual(len(data), buckets, "one candle per 5m bucket")
        times = [r["time"] for r in data]
        self.assertEqual(len(set(times)), buckets, "no repeated bucket")
        self.assertEqual(data[0]["close"], 101.0, "canonical HDFCBANK close wins")
        self.assertEqual(data[0]["volume"], 1000,
                         "canonical volume wins -- a duplicated bucket must not double it")
        self.assertNotEqual(data[0]["close"], 201.0)

    def test_five_minute_page_fills_distinct_buckets(self):
        base, buckets = self._seed_5m([("HDFCBANK", 0), ("HDFCBANK.NS", 100)], buckets=10)
        res = main.get_intraday_paginated(
            ticker="HDFCBANK", interval="5m", before=self._before_after_the_window(base, buckets),
            after=None, limit=4, background_tasks=None, db=self.db())
        self.assertEqual(len(res["data"]), 4, "a 4-candle page must contain 4 DISTINCT buckets")
        self.assertEqual(len({r["time"] for r in res["data"]}), 4)

    def test_five_minute_alias_only_buckets_are_preserved(self):
        base = datetime.datetime(2026, 10, 6, 9, 15)
        rows = []
        for i in range(4):                       # both spellings
            ts = base + datetime.timedelta(minutes=5 * i)
            rows.append(("HDFCBANK", "5m", ts, 100, 101, 99, 100.5, 1000))
            rows.append(("HDFCBANK.NS", "5m", ts, 200, 201, 199, 200.5, 2000))
        for i in range(4, 6):                    # alias-only buckets
            ts = base + datetime.timedelta(minutes=5 * i)
            rows.append(("HDFCBANK.NS", "5m", ts, 250, 251, 249, 250.5, 2500))
        self.seed(rows)

        before = int((base + datetime.timedelta(minutes=45)).replace(tzinfo=IST).timestamp())
        res = main.get_intraday_paginated(ticker="HDFCBANK", interval="5m", before=before,
                                          after=None, limit=600, background_tasks=None, db=self.db())
        self.assertEqual(len(res["data"]), 6)
        self.assertEqual(res["data"][0]["volume"], 1000)
        self.assertEqual(res["data"][-1]["volume"], 2500, "alias-only bucket kept")


    def test_after_cursor_excludes_older_buckets_and_keeps_contract(self):
        """`after` was previously untested. It must: exclude every bucket at or before
        the cursor, stay ascending, keep timestamps unique, still prefer the canonical
        row, and report has_more against what remains."""
        base = datetime.datetime(2026, 10, 6, 9, 15)
        rows = []
        for i in range(8):                       # 09:15 .. 09:50
            ts = base + datetime.timedelta(minutes=5 * i)
            rows.append(("TCS", "5m", ts, 100 + i, 101 + i, 99 + i, 100.5 + i, 10))
            rows.append(("TCS.NS", "5m", ts, 200 + i, 201 + i, 199 + i, 200.5 + i, 20))
        self.seed(rows)

        cursor = base + datetime.timedelta(minutes=15)          # 09:30, excluded by strict >
        cursor_epoch = int(cursor.replace(tzinfo=IST).timestamp())
        after_the_window = int((base + datetime.timedelta(hours=3)).replace(tzinfo=IST).timestamp())

        # full remaining page
        res = main.get_intraday_paginated(
            ticker="TCS", interval="5m", before=after_the_window, after=cursor_epoch,
            limit=600, background_tasks=None, db=self.db())
        data = res["data"]
        times = [r["time"] for r in data]
        self.assertEqual(len(times), 4, "the cursor bucket is excluded: 09:35..09:50 remain")
        self.assertEqual(times, sorted(times), "ascending output")
        self.assertEqual(len(set(times)), len(times), "unique timestamps")
        self.assertEqual(len([t for t in times if t <= cursor_epoch]), 0,
                         "no bucket at or before the cursor may leak in (strict >)")
        self.assertFalse(res["has_more"], "fewer than limit remain -> no more pages")
        closes = sorted({r["close"] for r in data})
        self.assertEqual(closes, sorted([100.5 + i for i in range(4, 8)]),
                         "canonical closes only (100.5+i, never the .NS 200.5+i)")

        # small page: newest two of the remaining five, and there IS more
        res2 = main.get_intraday_paginated(
            ticker="TCS", interval="5m", before=after_the_window, after=cursor_epoch,
            limit=2, background_tasks=None, db=self.db())
        times2 = [r["time"] for r in res2["data"]]
        self.assertEqual(len(times2), 2)
        self.assertTrue(res2["has_more"], "3 buckets remain beyond a 2-bar page")
        self.assertEqual(times2, sorted(times2))
        self.assertEqual(len([t for t in times2 if t <= cursor_epoch]), 0)
        self.assertEqual(times2, times[-2:], "the newest two of the remaining page")

    def test_daily_page_after_cursor(self):
        """Same contract through the paginated daily builder (`before` + limit)."""
        days = _recent_weekdays(8)
        rows = []
        for i, d in enumerate(days):
            rows.append(("TCS", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
            rows.append(("TCS.NS", "1D", d, 200 + i, 201 + i, 199 + i, 200.5 + i, 2000))
        self.seed(rows)

        res = _route(main.get_range_paginated)(None, ticker="TCS", range="ALL",
                                              before=str(days[4].date()), after=None,
                                              limit=10, db=self.db())
        keys = [r["time"] for r in res["data"]]
        self.assertEqual(len(keys), 4, "before=<5th day> keeps the four older days")
        self.assertEqual(len(set(keys)), 4)
        self.assertEqual(keys, sorted(keys), "ascending")
        self.assertFalse(res["has_more"])
        self.assertEqual(sorted({r["close"] for r in res["data"]}),
                         sorted([100.5 + i for i in range(4)]),
                         "canonical rows only")


class HelperUnitTests(_AliasFixture):
    class _Row:
        def __init__(self, ticker, ts, close, rid):
            self.ticker, self.timestamp, self.close, self.id = ticker, ts, close, rid

    def test_rank_prefers_canonical_then_row_id(self):
        ts = datetime.datetime(2026, 10, 6)
        rows = [self._Row("TCS.NS", ts, 2.0, 2), self._Row("TCS", ts, 1.0, 1)]
        out = main._dedupe_candles_prefer_canonical(rows, ["TCS", "TCS.NS"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].close, 1.0)

    def test_unknown_spelling_never_outranks_a_known_one(self):
        ts = datetime.datetime(2026, 10, 6)
        rows = [self._Row("SOMETHINGELSE", ts, 9.0, 99), self._Row("TCS.NS", ts, 2.0, 2)]
        out = main._dedupe_candles_prefer_canonical(rows, ["TCS", "TCS.NS"])
        self.assertEqual(out[0].close, 2.0)

    def test_duplicate_unknown_spellings_are_still_deterministic(self):
        ts = datetime.datetime(2026, 10, 6)
        rows = [self._Row("X", ts, 1.0, 1), self._Row("Y", ts, 2.0, 2)]
        out = main._dedupe_candles_prefer_canonical(rows, ["TCS"])
        self.assertEqual(out[0].close, 2.0, "same rank -> higher id wins")

    def test_limit_keeps_the_newest_and_output_is_descending(self):
        rows = [self._Row("TCS", datetime.datetime(2026, 10, d), float(d), d) for d in (1, 2, 3, 4)]
        out = main._dedupe_candles_prefer_canonical(rows, ["TCS"], limit=2)
        self.assertEqual([r.close for r in out], [4.0, 3.0])

    def test_suffixed_only_row_is_kept(self):
        ts = datetime.datetime(2026, 10, 6)
        out = main._dedupe_candles_prefer_canonical([self._Row("TCS.NS", ts, 5.0, 1)], ["TCS", "TCS.NS"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].close, 5.0)

    def test_page_fetch_limit_scales_but_is_never_below_what_was_asked(self):
        self.assertEqual(main._page_fetch_limit(6, ["TCS"]), 6)
        self.assertEqual(main._page_fetch_limit(6, ["TCS", "TCS.NS"]), 12)
        self.assertEqual(main._page_fetch_limit(6, ["NIFTY", "NIFTY50", "NIFTY 50", "^NSEI"]), 24)
        self.assertEqual(main._page_fetch_limit(50000, ["TCS", "TCS.NS"]), 60000, "capped for a 1 GB box")
        self.assertEqual(main._page_fetch_limit(50000, ["TCS", "TCS.NS", "X", "Y"]), 60000)


class HistoryBeforeContractTests(_AliasFixture):
    """`history-before` reuses _stock_data_range_core for 1D, so it inherits the
    dedupe. Its RESPONSE FORMAT is deliberately NOT changed by this fix: for 1D it
    converts every date to IST-midnight EPOCH SECONDS, while /api/stock-data/range
    returns the same candle's time as a "YYYY-MM-DD" STRING. These assertions pin
    the current behaviour so the inconsistency has to be changed on purpose."""

    def test_history_before_is_deduped_and_keeps_epoch_seconds(self):
        days = _recent_weekdays(12)
        rows = []
        for i, d in enumerate(days):
            rows.append(("TCS", "1D", d, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000))
            rows.append(("TCS.NS", "1D", d, 200 + i, 201 + i, 199 + i, 200.5 + i, 2000))
        self.seed(rows)

        res = _route(main.get_history_before)(None, ticker="TCS", timeframe="1D", before=None, limit=1500, db=self.db())
        times = [c["time"] for c in res["data"]]
        self.assertEqual(len(times), 12, "deduped here too")
        self.assertEqual(len(set(times)), 12)
        self.assertTrue(all(isinstance(t, int) for t in times),
                        "DOCUMENTED (unchanged): history-before returns EPOCH SECONDS for 1D")
        # the contrast, pinned on purpose
        daily = main._stock_data_range_core("TCS", "ALL", self.db())
        self.assertTrue(all(isinstance(r["time"], str) for r in daily),
                        "DOCUMENTED (unchanged): /range returns 'YYYY-MM-DD' strings")
        self.assertEqual(
            times[0],
            int(daily[0]["time"] and datetime.datetime.strptime(daily[0]["time"], "%Y-%m-%d")
                .replace(tzinfo=IST).timestamp()),
            "the same candle, two different time encodings")


if __name__ == "__main__":
    unittest.main()
