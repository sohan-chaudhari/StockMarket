"""Regression test: a lazy-load (before=...) page must not 500.

Symptom (reported): on a chart (e.g. INFY 5m) scrolling left loaded no candles
older than the first page (~28 Sep for a 600-bar page).

Cause: in `get_intraday_paginated`, `fill_needed` / `fill_start` were assigned
only INSIDE `if is_latest_request:`, but read afterwards at
`if fill_needed: bf_status = "backfilling"`. A paginated request
(`before=...` -> is_latest_request False) skips that whole block, so the read
raised UnboundLocalError -> HTTP 500 on every lazy-load page.

This test drives the paginated path with an in-memory SQLite `candles` table, so
it runs without PostgreSQL.
"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import models


class IntradayPaginationLazyLoadTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Candle.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _call(self, **overrides):
        kwargs = dict(ticker="TESTPAGE", interval="5m", before=1790586600,
                      after=None, limit=100, background_tasks=None)
        kwargs.update(overrides)
        db = self.Session()
        try:
            return main.get_intraday_paginated(db=db, **kwargs)
        finally:
            db.close()

    def test_paginated_request_returns_instead_of_raising(self):
        res = self._call()
        self.assertIn("data", res)
        self.assertIn("has_more", res)
        self.assertEqual(res["backfill_status"], "idle",
                         "a before=... page must not try to report a backfill")

    def test_paginated_request_never_reports_backfilling(self):
        # The gap-fill block is skipped entirely for pagination, so the status
        # must stay "idle" rather than being derived from an unbound variable.
        res = self._call(before=1700000000, limit=10)
        self.assertEqual(res["backfill_status"], "idle")

    def test_fill_needed_is_initialised_outside_the_latest_request_branch(self):
        # Guard the scoping rule itself, so a future refactor cannot move the
        # initialisation back inside the branch.
        import inspect
        src = inspect.getsource(main.get_intraday_paginated)
        i_init = src.find("fill_needed = False")
        i_branch = src.find("if is_latest_request:")
        self.assertNotEqual(i_init, -1, "fill_needed initialisation missing")
        self.assertNotEqual(i_branch, -1, "is_latest_request branch missing")
        self.assertLess(i_init, i_branch,
                        "fill_needed must be initialised before the branch")


if __name__ == "__main__":
    unittest.main()
