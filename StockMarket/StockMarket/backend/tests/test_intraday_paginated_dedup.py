"""Issue B regression — /api/stock-data/intraday/paginated merge dedup.

Proves the endpoint returns exactly ONE candle per timestamp, preferring the
stored/native candle over a candle resampled from a lower timeframe, in ascending
order — including the exact tip-collision case (pandas label='left' puts 5m bars
just after the last stored bar back into the same bucket label).

Runs entirely against an in-memory SQLite database: NO production/external
database row is read or written.
"""
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
import main
from database import Base


def _mk_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class IntradayPaginatedDedupTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_session()
        # The "latest request" path can trigger a network gap-fill; neutralise it
        # so the tests are pure DB/query assertions.
        self._bf = main.perform_on_demand_backfill
        main.perform_on_demand_backfill = lambda *a, **k: None

    def tearDown(self):
        main.perform_on_demand_backfill = self._bf
        self.db.close()

    def _seed(self, ticker, timeframe, rows):
        for ts, o, h, l, c, v in rows:
            self.db.add(models.Candle(
                ticker=ticker, timeframe=timeframe, timestamp=ts,
                open=o, high=h, low=l, close=c, volume=v, is_completed=True,
            ))
        self.db.commit()

    def _call(self, ticker, interval, limit=600):
        return main.get_intraday_paginated(
            ticker=ticker, interval=interval, before=None, after=None,
            limit=limit, background_tasks=None, db=self.db,
        )

    # -- Test 1 + production case: tip collision, stored/native wins -----------
    def test_tip_collision_stored_wins(self):
        tk = "ZZDEDUP15"
        T = datetime(2026, 3, 2, 10, 0)          # 15m bucket start
        self._seed(tk, "15m", [
            (datetime(2026, 3, 2, 9, 15), 100.0, 101.0, 99.0, 100.5, 111),
            (datetime(2026, 3, 2, 9, 30), 100.5, 102.0, 100.0, 101.0, 222),
            (datetime(2026, 3, 2, 9, 45), 101.0, 103.0, 100.5, 102.0, 333),
            (T, 1213.9, 1216.0, 1210.6, 1215.6, 187220),   # STORED (must win)
        ])
        # 5m bars AFTER the newest stored 15m -> resample bucket label == T
        self._seed(tk, "5m", [
            (datetime(2026, 3, 2, 10, 5), 1213.9, 1214.1, 1210.3, 1214.1, 60000),
            (datetime(2026, 3, 2, 10, 10), 1214.1, 1216.0, 1212.0, 1214.0, 70000),
        ])

        data = self._call(tk, "15m")["data"]
        times = [c["time"] for c in data]
        self.assertEqual(len(times), len(set(times)), "must not contain duplicate timestamps")

        t_epoch = main._ts_to_epoch(T)
        self.assertEqual(times.count(t_epoch), 1, "exactly one candle for the colliding 15m bucket")
        row = next(c for c in data if c["time"] == t_epoch)
        self.assertEqual(row["close"], 1215.6, "stored/native close must win over resampled")
        self.assertEqual(row["volume"], 187220, "stored/native volume must win over resampled")

    # -- Test 2: no duplicate timestamps across 15m/30m/1h --------------------
    def test_no_duplicate_timestamps_multi_tf(self):
        cases = {
            "15m": (datetime(2026, 3, 2, 10, 0), timedelta(minutes=15)),
            "30m": (datetime(2026, 3, 2, 10, 15), timedelta(minutes=30)),
            "1h":  (datetime(2026, 3, 2, 10, 15), timedelta(minutes=60)),
        }
        for interval, (T, step) in cases.items():
            tk = "ZZDEDUP" + interval.upper()
            self._seed(tk, interval, [
                (T - step, 100.0, 101.0, 99.0, 100.5, 100),
                (T, 1213.9, 1216.0, 1210.6, 1215.6, 187220),
            ])
            self._seed(tk, "5m", [
                (T + timedelta(minutes=5), 1213.9, 1214.1, 1210.3, 1214.1, 60000),
                (T + timedelta(minutes=10), 1214.1, 1216.0, 1212.0, 1214.0, 70000),
            ])
            data = self._call(tk, interval)["data"]
            self.assertTrue(data, f"{interval}: endpoint returned no rows")
            times = [c["time"] for c in data]
            self.assertEqual(len(times), len(set(times)), f"{interval}: duplicate timestamps present")

    # -- Test 3: ascending order ---------------------------------------------
    def test_sorted_ascending(self):
        tk = "ZZDEDUPSORT"
        self._seed(tk, "15m", [
            (datetime(2026, 3, 2, 9, 15), 100.0, 101.0, 99.0, 100.5, 1),
            (datetime(2026, 3, 2, 9, 30), 100.5, 102.0, 100.0, 101.0, 2),
            (datetime(2026, 3, 2, 10, 0), 101.0, 103.0, 100.5, 102.0, 3),
        ])
        self._seed(tk, "5m", [(datetime(2026, 3, 2, 10, 5), 101.0, 102.0, 100.0, 101.0, 4)])
        times = [c["time"] for c in self._call(tk, "15m")["data"]]
        self.assertEqual(times, sorted(times), "output must be ascending")
        self.assertEqual(len(times), len(set(times)))

    # -- Test 4: non-colliding resampled candles are preserved ----------------
    def test_non_colliding_resampled_preserved(self):
        tk = "ZZDEDUPKEEP"
        self._seed(tk, "15m", [
            (datetime(2026, 3, 2, 9, 15), 100.0, 101.0, 99.0, 100.5, 1),
            (datetime(2026, 3, 2, 9, 30), 100.5, 102.0, 100.0, 101.0, 2),
            (datetime(2026, 3, 2, 9, 45), 101.0, 103.0, 100.5, 102.0, 3),
        ])  # newest stored 09:45
        self._seed(tk, "5m", [   # after 09:45 -> 15m bucket 10:00 (no collision)
            (datetime(2026, 3, 2, 10, 0), 102.0, 103.0, 101.0, 102.5, 4),
            (datetime(2026, 3, 2, 10, 5), 102.5, 104.0, 102.0, 103.0, 5),
            (datetime(2026, 3, 2, 10, 10), 103.0, 104.0, 102.5, 103.5, 6),
        ])
        data = self._call(tk, "15m")["data"]
        times = [c["time"] for c in data]
        self.assertEqual(len(times), 4, "3 stored + 1 resampled must all be present (no loss)")
        self.assertIn(main._ts_to_epoch(datetime(2026, 3, 2, 10, 0)), times,
                      "extended (resampled) history must be retained")
        self.assertEqual(len(times), len(set(times)))

    # -- Test 5: 5m is unaffected --------------------------------------------
    def test_5m_unaffected(self):
        tk = "ZZDEDUP5M"
        rows = [(datetime(2026, 3, 2, 9, 15) + i * timedelta(minutes=5),
                 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10 + i) for i in range(6)]
        self._seed(tk, "5m", rows)
        data = self._call(tk, "5m")["data"]
        times = [c["time"] for c in data]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), len(set(times)))
        self.assertEqual(len(data), 6)


if __name__ == "__main__":
    unittest.main()
