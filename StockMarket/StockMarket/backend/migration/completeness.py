"""
1D historical completeness validation.
======================================================================
migration/validator.py's validate_source() checks per-fetch data QUALITY
(OHLC sanity, duplicates, date bounds, non-empty) but never checks whether
every trading session in a requested range is actually present. A chunk
that silently came back with zero candles due to a swallowed API error
(see historical_service.HistoricalFetchError) passed every one of those
checks and still produced a real historical gap -- this module is what
catches that class of defect.

Deliberately reuses exchange_calendar.nse_calendar -- the app's single
existing trading-day calendar -- rather than inventing a new one. It is
source-agnostic: it only compares "what trading days exist in [start, end]"
against "what candle rows actually exist for that ticker/timeframe in that
range", so it catches internal gaps, whole-chunk gaps, and the boundary
between old (e.g. YFINANCE) and newly-fetched (ANGELONE) data in one pass,
with no separate boundary-specific logic needed.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Dict, List, Tuple


def load_trading_calendar(db) -> int:
    """Loads NSE holiday dates from the DB (models.Holiday) into the shared
    exchange_calendar.nse_calendar singleton, the same source main.py's
    startup path uses. Safe to call repeatedly (idempotent replace, not
    additive). Returns the number of holiday rows loaded.

    CAVEAT (documented, not silently hidden): the `holidays` table was found
    EMPTY during this investigation, so is_trading_day() currently degrades
    to "not a weekend" -- no actual NSE holiday exclusion. This means a
    completeness check may report a handful of false-positive "missing
    sessions" on real market holidays (Diwali, Republic Day, etc.) until
    that table is populated -- a separate, out-of-scope task. Gaps of more
    than a handful of sessions cannot be explained by unpopulated holidays
    and are always a real defect.
    """
    from exchange_calendar import nse_calendar
    import models
    rows = db.query(models.Holiday.date).all()
    holidays = {r[0] for r in rows}
    nse_calendar.load_holidays(holidays)
    return len(holidays)


@dataclass
class GapInterval:
    start: date
    end: date
    missing_sessions: int


@dataclass
class CompletenessResult:
    ticker: str
    timeframe: str
    requested_start: date
    requested_end: date
    expected_sessions: int
    actual_sessions: int
    missing_sessions: int
    gaps: List[GapInterval] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.missing_sessions == 0

    def summary(self) -> str:
        if self.passed:
            return f"complete: {self.actual_sessions}/{self.expected_sessions} sessions present"
        return (f"INCOMPLETE: {self.missing_sessions} missing session(s) across "
                f"{len(self.gaps)} gap(s) (expected {self.expected_sessions}, "
                f"found {self.actual_sessions})")


def check_ticker_completeness(
    db, ticker: str, timeframe: str, requested_start: date, requested_end: date,
) -> CompletenessResult:
    """Read-only. Compares actual candle rows for [requested_start,
    requested_end] against the expected NSE trading sessions in that window
    (weekends + loaded holidays excluded). Reports every contiguous run of
    missing sessions as a GapInterval, regardless of where in the range it
    falls or which data_source produced the surrounding rows -- a gap at
    the old/new-data boundary is detected the same way as one in the
    middle of either source's own range."""
    from sqlalchemy import text
    from exchange_calendar import nse_calendar

    # Fetch raw timestamps and coerce to date() in Python rather than a
    # DB-specific date-cast in SQL -- keeps this portable across SQLite
    # (tests) and Postgres (production), matching promotion.py's
    # _coerce_timestamp() approach elsewhere in this package.
    #
    # Bind full datetime bounds, not bare dates: the `timestamp` column is
    # DATETIME, and a bare `date` end-bound compared as `<= :end` excludes
    # that day's own midnight row on SQLite (string comparison: the row's
    # "2024-01-05 00:00:00" sorts after the bound "2024-01-05"), silently
    # dropping the last requested day.
    from datetime import datetime as _dt, time as _time
    start_bound = _dt.combine(requested_start, _time.min)
    end_bound = _dt.combine(requested_end, _time.max)
    rows = db.execute(text("""
        SELECT timestamp FROM candles
        WHERE ticker = :t AND timeframe = :tf
          AND timestamp >= :start AND timestamp <= :end
    """), {"t": ticker, "tf": timeframe, "start": start_bound, "end": end_bound}).fetchall()
    actual_dates = {_as_date(r[0]) for r in rows}

    expected_sessions: List[date] = []
    d = requested_start
    while d <= requested_end:
        if nse_calendar.is_trading_day(d):
            expected_sessions.append(d)
        d += timedelta(days=1)

    present = [d in actual_dates for d in expected_sessions]

    gaps: List[GapInterval] = []
    i, n = 0, len(expected_sessions)
    while i < n:
        if not present[i]:
            j = i
            while j < n and not present[j]:
                j += 1
            gaps.append(GapInterval(expected_sessions[i], expected_sessions[j - 1], j - i))
            i = j
        else:
            i += 1

    missing_total = sum(g.missing_sessions for g in gaps)
    return CompletenessResult(
        ticker=ticker,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        expected_sessions=len(expected_sessions),
        actual_sessions=len(expected_sessions) - missing_total,
        missing_sessions=missing_total,
        gaps=gaps,
    )


def audit_completeness(
    db, tickers: List[Tuple[str, date, date]], timeframe: str = "1D",
) -> Dict[str, CompletenessResult]:
    """`tickers`: list of (ticker, requested_start, requested_end). Loads
    the trading calendar once, then checks each ticker. Read-only."""
    load_trading_calendar(db)
    return {
        ticker: check_ticker_completeness(db, ticker, timeframe, start, end)
        for ticker, start, end in tickers
    }


def missing_date_ranges(result: CompletenessResult) -> List[Tuple[date, date]]:
    return [(g.start, g.end) for g in result.gaps]


# ======================================================================
# Gap classification
# ======================================================================
# The `holidays` table is empty in production, so nse_calendar.is_trading_day()
# currently only excludes weekends -- which makes every real NSE holiday look
# like a missing session. Rather than invent a holiday list (there is no static
# holiday dataset in this repo, and the canonical populator,
# main.py::/api/holidays/refresh, needs a live NSE API call), classify each
# missing date from evidence ALREADY in the database:
#
#   if NO ticker in the entire universe has a candle that day, the market was
#   shut -- that is a market-wide closure, not this ticker's missing data.
#   If thousands of other tickers DO have that day, the absence is specific to
#   this security and must still be treated as a real gap.
#
# This deliberately cannot "hide" a genuine gap: hiding requires the whole
# market to be simultaneously absent, which is exactly what a closure is. Dates
# where the database itself is too thin to judge are reported as INDETERMINATE
# rather than silently absolved.

class GapCause(str, Enum):
    MARKET_CLOSURE = "market_closure"        # 0 tickers anywhere -> exchange shut (holiday)
    SECURITY_ABSENT = "security_absent"      # market open, this ticker has no candle -> REAL gap
    INDETERMINATE = "indeterminate"          # universe too sparse that day to judge


def daily_market_coverage(db, start: date, end: date, timeframe: str = "1D") -> Dict[date, int]:
    """Read-only. {date: number of DISTINCT tickers holding a candle that day}
    across the whole universe.

    Groups with the SQL `date()` function, which exists on both Postgres
    (production) and SQLite (tests). Deliberately NOT `cast(.., Date)`:
    SQLite has no real DATE type, so casting yields a value SQLAlchemy's Date
    result-processor then fails to parse ("fromisoformat: argument must be
    str"). Leaving the expression untyped returns whatever the driver gives
    -- str on SQLite, date on Postgres -- and _as_date() normalizes both."""
    from datetime import datetime as _dt, time as _time
    from sqlalchemy import func, distinct, select
    from models import Candle

    stmt = (
        select(func.date(Candle.timestamp), func.count(distinct(Candle.ticker)))
        .where(
            Candle.timeframe == timeframe,
            Candle.timestamp >= _dt.combine(start, _time.min),
            Candle.timestamp <= _dt.combine(end, _time.max),
        )
        .group_by(func.date(Candle.timestamp))
    )
    return {_as_date(d): n for d, n in db.execute(stmt).fetchall()}


def classify_missing_dates(
    db, missing_dates: List[date], window_start: date, window_end: date,
    timeframe: str = "1D", sparse_ratio: float = 0.10,
) -> Tuple[Dict[date, Tuple[GapCause, int]], int]:
    """Returns ({date: (cause, tickers_with_data_that_day)}, baseline_coverage).

    baseline_coverage is the median coverage across days the market clearly
    WAS open, so `sparse_ratio` adapts to however populated this database
    happens to be instead of relying on a hardcoded ticker count."""
    coverage = daily_market_coverage(db, window_start, window_end, timeframe)
    positives = sorted(v for v in coverage.values() if v > 0)
    if positives:
        baseline = positives[len(positives) // 2]
    else:
        baseline = 0

    out: Dict[date, Tuple[GapCause, int]] = {}
    for d in missing_dates:
        cov = coverage.get(d, 0)
        if cov == 0:
            cause = GapCause.MARKET_CLOSURE
        elif baseline and cov < baseline * sparse_ratio:
            cause = GapCause.INDETERMINATE
        else:
            cause = GapCause.SECURITY_ABSENT
        out[d] = (cause, cov)
    return out, baseline


def classify_result(db, result: CompletenessResult, sparse_ratio: float = 0.10) -> Dict[str, object]:
    """Breaks one CompletenessResult's missing sessions into causes. The raw
    missing_sessions count is preserved untouched -- this ADDS interpretation,
    it never rewrites the measurement."""
    dates: List[date] = []
    for g in result.gaps:
        d = g.start
        while d <= g.end:
            dates.append(d)
            d += timedelta(days=1)

    classified, baseline = classify_missing_dates(
        db, dates, result.requested_start, result.requested_end,
        result.timeframe, sparse_ratio,
    )
    counts = {c: 0 for c in GapCause}
    for cause, _cov in classified.values():
        counts[cause] += 1
    return {
        "ticker": result.ticker,
        "missing_total": result.missing_sessions,
        "market_closure": counts[GapCause.MARKET_CLOSURE],
        "security_absent": counts[GapCause.SECURITY_ABSENT],
        "indeterminate": counts[GapCause.INDETERMINATE],
        "unexplained": counts[GapCause.SECURITY_ABSENT] + counts[GapCause.INDETERMINATE],
        "baseline_coverage": baseline,
        "detail": classified,
    }


def _as_date(value) -> date:
    from datetime import datetime as _dt
    if isinstance(value, _dt):
        return value.date()
    if isinstance(value, date):
        return value
    return _dt.fromisoformat(str(value)).date()
