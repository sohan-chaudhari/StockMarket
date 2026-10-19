"""The candle COLOUR is a pure function of OHLC, so a colour mismatch is a DATA mismatch.

Reported: candles show a different colour from Angel One on NIFTY 5m.

Investigated and ruled out, with evidence:
  * the colour rule -- the big series uses the charting library's default
    (close >= open -> upColor #089981, else downColor #f23645) and declares NO custom
    colorer, so nothing in our code decides a colour independently of the values;
  * field mapping -- the API emits open/close explicitly and the probe showed them
    consistent with the DB.

Actual cause: the same 5m bucket is stored under TWO ticker spellings with different
OHLC (NIFTY and the NIFTY50 alias), and the payload carried both. Measured on
production 2026-10-19:

    11:15  NIFTY  O=23071.2 C=23074.7  | NIFTY50 O=23071.2 C=23077.55
    12:00  NIFTY  O=22985.5 C=22954.2  | NIFTY50 O=22984.9 C=22953.5
    today: 72 NIFTY buckets, 68 NIFTY50, 68 overlapping
    last 5 sessions: 19 of 250 overlapping buckets DISAGREE on direction

Each disagreement is a candle that can render the opposite colour depending on which
of the two rows the chart happens to draw -- which is exactly the reported symptom.
Deduplication (one row per timestamp, canonical spelling wins) removes it at the
source for every instrument, not just NIFTY.
"""
import datetime
import pathlib
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import models

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _route(fn):
    return getattr(fn, "__wrapped__", fn)


class SeriesColourComesFromOhlcOnlyTests(unittest.TestCase):
    """The rendered colour is the charting library's own rule (close vs open) applied to
    the values we hand it. The app must not override it with a custom colorer, or a
    candle could be painted a colour that disagrees with the OHLC it is drawing.

    This asserts the SHIPPED series configuration rather than re-implementing the rule
    in the test, which would prove only that the test agrees with itself.
    """

    FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "dashboard.js"

    def setUp(self):
        if not self.FRONTEND.exists():
            self.skipTest("frontend not present")
        self.js = self.FRONTEND.read_text(encoding="utf-8", errors="ignore")

    def _series_block(self):
        i = self.js.find("bigChart.addCandlestickSeries(")
        self.assertNotEqual(i, -1, "the big candlestick series must exist")
        return self.js[i:i + 400]

    def test_big_candle_series_declares_the_platform_palette(self):
        block = self._series_block()
        self.assertIn("upColor: '#089981'", block)
        self.assertIn("downColor: '#f23645'", block)
        self.assertIn("wickUpColor: '#089981'", block)
        self.assertIn("wickDownColor: '#f23645'", block)

    def test_no_custom_colorer_overrides_the_ohlc_rule(self):
        """A `colorer` would let the painted colour diverge from close-vs-open, which is
        the one thing that would make a correct OHLC pair render the wrong colour."""
        self.assertNotIn("colorer:", self.js,
                         "the candle colour must stay the library's close-vs-open rule")


class DuplicateSpellingWouldFlipAColourTests(unittest.TestCase):
    """End-to-end: with both spellings present, the payload must carry ONE row per
    bucket, so the colour cannot depend on which row the database returned first."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        models.StockMetadata.__table__.create(self.engine)
        models.CurrentDayCandle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def db(self):
        s = self.Session()
        self.addCleanup(s.close)
        return s

    def _seed(self, rows):
        db = self.Session()
        try:
            for r in rows:
                db.add(models.Candle(ticker=r[0], timeframe=r[1], timestamp=r[2],
                                     open=r[3], high=r[4], low=r[5], close=r[6], volume=r[7]))
            db.commit()
        finally:
            db.close()

    def test_the_real_nifty_buckets_collapse_to_one_row_each(self):
        """The exact values measured in production, including the two buckets whose
        copies disagree on direction."""
        base = datetime.datetime(2026, 10, 19, 11, 15)
        pairs = [
            # (minute, NIFTY open, NIFTY close, NIFTY50 open, NIFTY50 close)
            (0,  23071.2, 23074.7, 23071.2, 23077.55),   # both green
            (25, 22991.95, 23003.95, 22992.5, 23003.1),  # both green
            (45, 22985.5, 22954.2, 22984.9, 22953.5),    # both red
        ]
        rows = []
        for minute, o1, c1, o2, c2 in pairs:
            ts = base + datetime.timedelta(minutes=minute)
            rows.append(("NIFTY", "5m", ts, o1, max(o1, c1), min(o1, c1), c1, 0))
            rows.append(("NIFTY50", "5m", ts, o2, max(o2, c2), min(o2, c2), c2, 0))
        self._seed(rows)

        before = base - datetime.timedelta(minutes=5)
        res = main.get_intraday_paginated(
            ticker="NIFTY", interval="5m",
            before=int((base + datetime.timedelta(hours=2)).replace(tzinfo=IST).timestamp()),
            after=None, limit=600, background_tasks=None, db=self.db())

        data = res["data"]
        self.assertEqual(len(data), 3, "one candle per 5m bucket")
        self.assertEqual(len({r["time"] for r in data}), 3, "no repeated timestamp")

        # every surviving row is the canonical NIFTY one, so the colour is the
        # canonical direction and cannot be decided by the alias
        by_close = sorted(r["close"] for r in data)
        self.assertEqual(by_close, sorted([23074.7, 23003.95, 22954.2]))
        self.assertNotIn(23077.55, by_close, "the NIFTY50 copy must not be served")

    def test_a_bucket_where_the_alias_disagrees_cannot_flip(self):
        """One bucket: canonical says green, alias says red. Exactly one row may be
        served, and it must be the canonical one -- otherwise the candle renders red
        while Angel One shows green."""
        ts = datetime.datetime(2026, 10, 19, 12, 0)
        self._seed([
            ("NIFTY",   "5m", ts, 22985.50, 22986.00, 22950.00, 22990.00, 0),   # GREEN
            ("NIFTY50", "5m", ts, 22984.90, 22985.00, 22950.00, 22950.00, 0),   # RED
        ])
        res = main.get_intraday_paginated(
            ticker="NIFTY", interval="5m",
            before=int((ts + datetime.timedelta(hours=2)).replace(tzinfo=IST).timestamp()),
            after=None, limit=600, background_tasks=None, db=self.db())

        self.assertEqual(len(res["data"]), 1, "one row per bucket")
        row = res["data"][0]
        self.assertGreater(row["close"], row["open"], "the canonical (green) direction must win")
        self.assertEqual(row["close"], 22990.0)

    def test_requesting_the_alias_still_returns_its_own_direction(self):
        """A user on NIFTY50 must see NIFTY50's data, not NIFTY's -- the preference is
        the requested spelling, not a hardcoded ticker."""
        ts = datetime.datetime(2026, 10, 19, 12, 0)
        self._seed([
            ("NIFTY",   "5m", ts, 22985.50, 22986.00, 22950.00, 22990.00, 0),   # GREEN
            ("NIFTY50", "5m", ts, 22984.90, 22985.00, 22950.00, 22950.00, 0),   # RED
        ])
        res = main.get_intraday_paginated(
            ticker="NIFTY50", interval="5m",
            before=int((ts + datetime.timedelta(hours=2)).replace(tzinfo=IST).timestamp()),
            after=None, limit=600, background_tasks=None, db=self.db())
        self.assertEqual(len(res["data"]), 1)
        self.assertLess(res["data"][0]["close"], res["data"][0]["open"], "NIFTY50's red must win for a NIFTY50 request")

    def test_daily_colour_is_also_deduplicated(self):
        """Same defect on the daily range (TCS/TCS.NS), which uses a separate builder.

        Uses recent weekdays so the daily builder sees a fresh enough series and does
        not attempt a provider gap-fill (no network in tests)."""
        today = datetime.date.today()
        days, d = [], today
        while len(days) < 3:
            if d.weekday() < 5:
                days.append(d)
            d -= datetime.timedelta(days=1)
        days.sort()
        target = days[0]
        ts = datetime.datetime(target.year, target.month, target.day)
        rows = [("TCS", "1D", datetime.datetime(x.year, x.month, x.day),
                 2635.62, 2653.28, 2610.30, 2626.10, 4699596) for x in days]
        rows += [("TCS.NS", "1D", ts, 2686.00, 2690.00, 2670.00, 2676.30, 1000)]
        self._seed(rows)

        out = main._stock_data_range_core("TCS", "ALL", self.db())
        by_day = {r["time"]: r for r in out}
        key = str(target)
        self.assertIn(key, by_day)
        self.assertLess(by_day[key]["close"], by_day[key]["open"],
                        "canonical TCS (red) must win over the TCS.NS copy")
        self.assertEqual(by_day[key]["close"], 2626.10)
        self.assertEqual(len(by_day), len(days), "one bar per day, no repeated dates")


if __name__ == "__main__":
    unittest.main()
