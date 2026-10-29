"""The daily candle must come from the exchange, not from a 5m aggregate.

Measured 2026-10-28 against the exchange quote after the close (at that point the
quote *is* the session truth):

    ticker     exchange O / H / L / C                our 1D row
    CUPID      317.00 / 317.90 / 309.00 / 314.55     317.20 / 317.90 / 309.00 / 316.00
    TCS        2449.00 / 2461.30 / 2408.20 / 2460    2447.10 / 2479.00 / 2408.20 / 2460
    RELIANCE   1194.50 / 1204.30 / 1156.40 / 1158    exact match

Two independent defects produced that:

1. the 5m aggregate ran on a session that *lagged the clock* (CUPID had 32 of 74
   bars at 15:25, and the old gate was `min_bars=3`, passed by both live call
   sites), so a stalled download was treated as a finished session;
2. even a complete 5m session cannot carry the 15:30 closing-auction price, because
   our last bar is 15:25 -- so an aggregated close is the 15:25 price, always.
"""

import pathlib
import re
from datetime import date, datetime

import pytest

import daily_provider_refresh as dpr
import main

BK = pathlib.Path(__file__).resolve().parents[1]
SRC = (BK / "main.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. the completeness gate must follow the clock, not a constant
# --------------------------------------------------------------------------- #

def test_gate_rejects_a_download_that_stalled_late_in_the_day():
    """CUPID: 32 bars present at 15:25 while the session already spans 74."""
    gate = main._session_coverage_min_bars(now_ist=datetime(2026, 10, 28, 15, 25))
    assert gate > 32, "a 32-bar series must not count as a finished session"
    assert gate <= 70


def test_gate_accepts_a_genuine_mid_session_day():
    """At 11:55 the whole session so far really *is* ~32 bars."""
    gate = main._session_coverage_min_bars(now_ist=datetime(2026, 10, 28, 11, 55))
    assert gate <= 32


def test_gate_is_low_before_the_open_and_strict_after_the_close():
    assert main._session_coverage_min_bars(now_ist=datetime(2026, 10, 28, 8, 0)) == 3
    after = main._session_coverage_min_bars(now_ist=datetime(2026, 10, 28, 20, 0))
    assert 60 <= after <= 70, after


def test_no_caller_can_reopen_the_three_bar_bug():
    """`asyncio.to_thread(fn, days, 3)` has no parenthesis, so scan the arguments."""
    found = 0
    for m in re.finditer(r"_reconcile_daily_from_5m,\s*([^,\n]+),\s*([^\n)]+)", SRC):
        expr = m.group(2).strip()
        found += 1
        if expr.isdigit():
            assert int(expr) >= 60, f"caller passes min_bars={expr}"
    assert found >= 2, "expected the live + scheduled call sites"


# --------------------------------------------------------------------------- #
# 2. source authority: exchange rows win, and are never silently downgraded
# --------------------------------------------------------------------------- #

def test_5m_reconcile_refuses_to_downgrade_an_exchange_daily():
    i = SRC.index("def _reconcile_daily_from_5m(")
    body = SRC[i:i + 6000]
    assert "WHERE candles.data_source IS DISTINCT FROM 'ANGELONE'" in body, (
        "a 5m aggregate must never overwrite a daily that came from the exchange")


def test_quote_path_writes_angelone_rows_with_an_upsert():
    i = SRC.index("def _reconcile_daily_from_quote(")
    body = SRC[i:i + 5000]
    assert "ON CONFLICT ON CONSTRAINT uix_candle_key DO UPDATE" in body
    assert "'ANGELONE'" in body
    # a baseline read back out of our own DB is not evidence about the exchange
    assert "_tick_is_current_session(raw, market_open)" in body


def test_quote_path_skips_indices_and_inconsistent_ticks():
    i = SRC.index("def _reconcile_daily_from_quote(")
    body = SRC[i:i + 5000]
    assert "if tk in _INDEX_LIST:" in body
    assert "h < max(o, c) - 0.01 or l > min(o, c) + 0.01" in body or \
           "h < max(o, c)" in body


# --------------------------------------------------------------------------- #
# 3. the ONE_DAY provider refresher is wired in, after the close
# --------------------------------------------------------------------------- #

def test_provider_refresh_runs_after_the_close():
    i = SRC.index("async def _daily_bar_refresh_loop(")
    body = SRC[i:i + 2600]
    assert "daily_provider_refresh" in body
    assert "(15, 35)" in body, "ONE_DAY must only be consulted after the close"
    assert "_reconcile_daily_from_quote" in body


def test_the_daily_refresher_never_uses_the_priority_path():
    """priority=True bypasses the shared limiter; the broker then answers 0 rows.

    Measured 2026-10-29: with priority=True the first call returned 2 rows and every
    call after it returned 0 rows in 0.00 s, so a 3-ticker run wrote one row and looked
    like missing data. With the default pacing each call blocks ~15 s and returns data.
    """
    i = SRC.index("async def _daily_bar_refresh_loop(")
    body = SRC[i:SRC.index("\nasync def ", i + 10)]
    assert "_dpr.refresh, 60, 240.0, 3" in body, "the call must use positional args, no priority"
    mod = (BK / "daily_provider_refresh.py").read_text(encoding="utf-8")
    assert "priority=False" in mod[mod.index("def main():"):]





# --------------------------------------------------------------------------- #
# 4. provider candle validation -- never write an impossible daily
# --------------------------------------------------------------------------- #

def test_clean_candle_accepts_a_real_daily():
    row = dpr.clean_candle({"timestamp": datetime(2026, 10, 28), "open": 317.0,
                            "high": 317.9, "low": 309.0, "close": 314.55,
                            "volume": 10735425})
    assert row == {"open": 317.0, "high": 317.9, "low": 309.0,
                   "close": 314.55, "volume": 10735425}


@pytest.mark.parametrize("bad", [
    {"open": 317.0, "high": 300.0, "low": 309.0, "close": 314.55},   # high < open
    {"open": 317.0, "high": 320.0, "low": 318.0, "close": 314.55},   # low > close
    {"open": 0, "high": 317.9, "low": 309.0, "close": 314.55},       # zero open
    {"open": 317.0, "high": 317.9, "low": 309.0, "close": 0},        # zero close
    {"open": None, "high": None, "low": None, "close": None},
    "not-a-candle",
])
def test_clean_candle_rejects_impossible_dailies(bad):
    assert dpr.clean_candle(bad) is None


def test_clean_candle_clamps_negative_volume():
    assert dpr.clean_candle({"open": 10, "high": 11, "low": 9, "close": 10,
                             "volume": -5})["volume"] == 0


def test_select_rows_keeps_only_the_window_and_normalises_the_timestamp():
    rows = dpr.select_rows([
        {"timestamp": datetime(2026, 10, 28, 0, 0), "open": 1, "high": 2, "low": 1, "close": 2},
        {"timestamp": datetime(2026, 9, 1, 0, 0), "open": 1, "high": 2, "low": 1, "close": 2},
        {"timestamp": "2026-10-27T00:00:00", "open": 1, "high": 2, "low": 1, "close": 2},
        {"timestamp": "garbage", "open": 1, "high": 2, "low": 1, "close": 2},
    ], start_day=date(2026, 10, 27), today=date(2026, 10, 28))
    assert [r["ts"] for r in rows] == [datetime(2026, 10, 28), datetime(2026, 10, 27)]


# --------------------------------------------------------------------------- #
# 5. refresh() end to end with fakes (no network, no database)
# --------------------------------------------------------------------------- #

class _FakeDB:
    def __init__(self):
        self.executed = 0
        self.commits = 0
        self.closed = False

    def execute(self, stmt, params=None):  # noqa: ARG002
        self.executed += 1
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class _FakeSvc:
    def __init__(self, candles, logged_in=True):
        self.candles = candles
        self.is_logged_in = logged_in

    def login(self):
        pass

    def get_historical_candles(self, **kwargs):  # noqa: ARG002
        return self.candles


@pytest.fixture
def fakes():
    db = _FakeDB()
    dpr._session_factory_override = lambda: db
    dpr._clock_override = lambda: datetime(2026, 10, 29, 6, 55)   # fixed "today"
    yield db
    dpr._session_factory_override = None
    dpr._service_override = None
    dpr._clock_override = None


def test_refresh_writes_the_good_row_and_skips_the_impossible_one(fakes):
    dpr._service_override = _FakeSvc([
        {"timestamp": datetime(2026, 10, 28), "open": 317.0, "high": 317.9,
         "low": 309.0, "close": 314.55, "volume": 10735425},
        {"timestamp": datetime(2026, 10, 28), "open": 317.0, "high": 300.0,
         "low": 309.0, "close": 314.55, "volume": 1},
    ])
    stats = dpr.refresh(only_tickers=["CUPID"], budget_seconds=5.0)
    assert stats["rows"] == 1, stats
    assert stats["tickers"] == 1
    assert fakes.commits == 1
    assert fakes.closed


def test_refresh_is_a_no_op_without_a_provider_login(fakes):
    dpr._service_override = _FakeSvc([], logged_in=False)
    stats = dpr.refresh(only_tickers=["CUPID"], budget_seconds=5.0)
    assert stats["rows"] == 0
    assert stats["reason"] == "not_logged_in"


def test_refresh_never_raises_when_the_provider_throws(fakes):
    class _Boom(_FakeSvc):
        def get_historical_candles(self, **kwargs):
            raise RuntimeError("provider down")

    dpr._service_override = _Boom([])
    stats = dpr.refresh(only_tickers=["CUPID"], budget_seconds=5.0)
    assert stats["rows"] == 0
    assert stats["errors"] == 1


def test_candidates_only_return_rows_that_still_need_the_exchange():
    """Convergence depends on this: a fully-ANGELONE window must not be re-queued."""
    src = (BK / "daily_provider_refresh.py").read_text(encoding="utf-8")
    i = src.index("def candidates(")
    body = src[i:src.index("def _upsert(", i)]
    sql = body[body.index('sa_text("""') + 11:body.index('"""', body.index('sa_text("""') + 12)]
    assert "NOT EXISTS" in sql, "a never-written ticker must be included"
    assert "c.data_source IN ('5M_AGG', 'BACKFILL', 'YFINANCE')" in sql
    assert "ANGELONE" not in sql, "rows already from the exchange must not be re-fetched"
    # `timestamp::date` cannot use the index and measured 30 s instead of 2.9 s
    assert "::date" not in sql


def test_the_candidate_list_is_fetched_once_per_cycle_not_once_per_pass(fakes):
    """Each pass must spend its time on the provider, not on the queue query."""
    calls = []

    def _fake_candidates(db, start_day, today, limit):
        calls.append(limit)
        return ["AAA", "BBB", "CCC"]

    original = dpr.candidates
    dpr.candidates = _fake_candidates
    try:
        class _Svc(_FakeSvc):
            def get_historical_candles(self, **kwargs):
                return [{"timestamp": datetime(2026, 10, 28), "open": 10.0, "high": 11.0,
                         "low": 9.0, "close": 10.5, "volume": 100}]

        dpr._service_override = _Svc([])
        first = dpr.refresh(limit=1, budget_seconds=5.0)
        second = dpr.refresh(limit=1, budget_seconds=5.0)
    finally:
        dpr.candidates = original
    assert first["tickers"] == 1 and second["tickers"] == 1
    assert len(calls) == 1, "the queue query ran %d times" % len(calls)


def test_the_real_service_is_the_instance_not_the_module():
    """`historical_service.login()` lives on an instance; the module has no `.login()`.

    The first production run failed with "module 'historical_service' has no attribute
    'login'" and quietly wrote nothing, so assert the shape we actually get.
    """
    svc = dpr._get_service()
    assert hasattr(svc, "login") and callable(svc.login)
    assert hasattr(svc, "is_logged_in")
    assert hasattr(svc, "get_historical_candles")


def test_refresh_asks_for_a_window_wider_than_the_days_it_writes(fakes):
    """The provider drops the window endpoints (measured 2026-10-29).

    ONE_DAY with date(28)..date(28) returned 0 rows for a day that exists, and
    date(27)..date(29) returned only the 28th -- so a request for exactly the days we
    want silently yields nothing. Ask a day either side and filter locally.
    """
    seen = {}

    class _Recording(_FakeSvc):
        def get_historical_candles(self, **kwargs):
            seen.update(kwargs)
            return [{"timestamp": datetime(2026, 10, 28), "open": 317.0, "high": 317.9,
                     "low": 309.0, "close": 314.55, "volume": 10735425}]

    dpr._service_override = _Recording([])
    stats = dpr.refresh(only_tickers=["CUPID"], budget_seconds=5.0, days_back=1)
    assert stats["rows"] == 1
    # target window is 28..29 Oct; the request must reach a day either side of it
    assert seen["from_date"] <= date(2026, 10, 27)
    assert seen["to_date"] >= date(2026, 10, 30)
