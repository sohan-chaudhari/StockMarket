"""Reliable 5-minute historical gap repair.

Two things live here:

1. **Exact, session-aware missing-bucket detection.** Given a ticker and a range
   it computes the 5-minute buckets the NSE session actually defines (09:15 ..
   15:25 IST, trading days only, the still-forming bucket excluded) and subtracts
   what is stored. Only genuinely missing buckets become a fetch range -- so
   weekends, holidays, out-of-session time and the current incomplete candle are
   never "repaired", and a short chart-open range can never stand in for an older
   historical gap.

2. **Persistent, leased jobs** (`backfill_jobs`) so a repair survives restarts and
   cannot be run twice concurrently. A job is only `complete` after the persisted
   rows are re-read and the expected buckets are confirmed present.

All provider access goes through historical_service.get_historical_candles(),
which owns the account-wide pacing, the shared cooldown and request dedup.
"""
import datetime as _dt
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import database

# Angel One returns candle timestamps with an explicit offset
# ("%Y-%m-%dT%H:%M:%S%z"), so they arrive tz-AWARE while every stored bucket and
# every expected bucket is IST-naive. Comparing the two raises TypeError, which
# silently stopped the bracketing test from ever classifying an interval -- so the
# provider's timestamps are normalised to IST-naive before any comparison.
_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_naive(v):
    """Normalise a provider timestamp to IST-naive (no-op when already naive)."""
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(_IST).replace(tzinfo=None)
    return v

# ── tunables (env, no code change needed) ──────────────────────────────────
JOB_LEASE_SEC = int(os.getenv("BACKFILL_JOB_LEASE_SEC", "600"))
JOB_MAX_ATTEMPTS = int(os.getenv("BACKFILL_JOB_MAX_ATTEMPTS", "6"))
JOB_BASE_BACKOFF_SEC = int(os.getenv("BACKFILL_JOB_BASE_BACKOFF_SEC", "120"))
JOB_MAX_BACKOFF_SEC = int(os.getenv("BACKFILL_JOB_MAX_BACKOFF_SEC", "3600"))
JOB_RETENTION_DAYS = int(os.getenv("BACKFILL_JOB_RETENTION_DAYS", "14"))
# A job that yielded to the SHARED provider cooldown must come back quickly. With
# the normal exponential backoff (120s * 2^n, jittered) a throttled job retried
# only every 8-16 minutes -- measured: TATACAP's genuine 8-session hole (581
# buckets) was attempted 4 times in 4 hours and persisted NOTHING, because every
# attempt hit an active cooldown. Throttle yields now retry in ~20s..cooldown+5s.
THROTTLE_RETRY_SEC = int(os.getenv("BACKFILL_THROTTLE_RETRY_SEC", "20"))
# Provider maximum span per request, by interval (mirrors perform_on_demand_backfill).
MAX_SPAN_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 730}

SESSION_START = (9, 15)     # NSE opens 09:15 IST
SESSION_END = (15, 30)      # ... and closes 15:30 IST

# Missing buckets closer together than this are fetched in ONE request (see
# missing_ranges). Keeps an illiquid ticker with scattered holes from turning into
# hundreds of tiny provider calls.
COALESCE_GAP = timedelta(days=4)

_DDL = """
CREATE TABLE IF NOT EXISTS backfill_jobs (
    id               BIGSERIAL PRIMARY KEY,
    ticker           VARCHAR(32)  NOT NULL,
    exchange         VARCHAR(8)   NOT NULL DEFAULT 'NSE',
    interval         VARCHAR(8)   NOT NULL,
    range_start      TIMESTAMP    NOT NULL,
    range_end        TIMESTAMP    NOT NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'queued',
    attempts         INTEGER      NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMP,
    last_attempt_at  TIMESTAMP,
    lease_expires_at TIMESTAMP,
    claimed_by       VARCHAR(64),
    last_error       VARCHAR(400),
    failure_kind     VARCHAR(24),
    expected_count   INTEGER,
    fetched_count    INTEGER,
    persisted_count  INTEGER,
    remaining_count  INTEGER,
    created_at       TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP    NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uix_backfill_jobs_open
    ON backfill_jobs (ticker, exchange, interval, range_start, range_end)
    WHERE status IN ('queued','running','retry_wait','partially_complete');
CREATE INDEX IF NOT EXISTS ix_backfill_jobs_claimable
    ON backfill_jobs (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_backfill_jobs_ticker
    ON backfill_jobs (ticker, interval);

-- Buckets that cannot be repaired, recorded so they are never re-fetched.
-- `reason` separates two very different things:
--   provider_absent -- PROVEN: a successful provider response bracketed the
--                      bucket (returned data before AND after it) and omitted
--                      it, which rules out a truncated range.
--   pre_data        -- the bucket predates any observed data for the ticker
--                      (or its listing date when the schema provides one). It is
--                      deliberately NOT labelled provider_absent, because with no
--                      listing metadata a pre-listing period is indistinguishable
--                      from a gap.
CREATE TABLE IF NOT EXISTS backfill_absent (
    id           BIGSERIAL PRIMARY KEY,
    ticker       VARCHAR(32)  NOT NULL,
    exchange     VARCHAR(8)   NOT NULL DEFAULT 'NSE',
    interval     VARCHAR(8)   NOT NULL,
    bucket       TIMESTAMP    NOT NULL,
    reason       VARCHAR(24)  NOT NULL,
    evidence     VARCHAR(200),
    confirmed_at TIMESTAMP    NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uix_backfill_absent
    ON backfill_absent (ticker, exchange, interval, bucket);
CREATE INDEX IF NOT EXISTS ix_backfill_absent_lookup
    ON backfill_absent (ticker, interval, bucket);
"""

_ddl_lock = threading.Lock()
_ddl_done = False


def ensure_table():
    """Idempotent DDL (safe on every start; no migration runner required)."""
    global _ddl_done
    if _ddl_done:
        return
    with _ddl_lock:
        if _ddl_done:
            return
        db = database.SessionLocal()
        try:
            for stmt in _DDL.strip().split(";"):
                if stmt.strip():
                    db.execute(text(stmt))
            db.commit()
            _ddl_done = True
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Session-aware expected buckets
# ─────────────────────────────────────────────────────────────────────────────
def _is_trading_day(d) -> bool:
    try:
        from main import is_trading_day, NSE_HOLIDAYS
        return bool(is_trading_day(d, NSE_HOLIDAYS))
    except Exception:
        return d.weekday() < 5


def session_buckets(day) -> list:
    """Every 5-minute bucket label the NSE session defines for `day` (IST-naive)."""
    out = []
    cur = datetime(day.year, day.month, day.day, SESSION_START[0], SESSION_START[1])
    end = datetime(day.year, day.month, day.day, SESSION_END[0], SESSION_END[1])
    while cur < end:
        out.append(cur)
        cur = cur + timedelta(minutes=5)
    return out


def expected_buckets(range_start, range_end, now=None) -> list:
    """All 5m buckets a correct store must hold in [range_start, range_end].

    Excludes weekends/holidays, anything outside 09:15..15:30, and any bucket that
    has not finished yet -- the still-forming bucket AND every future bucket of the
    current session. Job windows end at end-of-day (see main._stable_job_range), so
    without this the buckets later in today's session would be reported as missing
    until the close, and the job could never reach `complete`.
    """
    now = now or database.get_ist_now()
    out = []
    d = range_start.date()
    last = range_end.date()
    while d <= last:
        if _is_trading_day(d):
            for b in session_buckets(d):
                if b < range_start or b > range_end:
                    continue
                if b + timedelta(minutes=5) > now:
                    continue          # still forming, or not started yet
                out.append(b)
        d = d + timedelta(days=1)
    return out


def _as_dt(v):
    """Normalise a driver-returned timestamp to a datetime.

    Raw `text()` queries bypass SQLAlchemy's type coercion, so the driver hands
    back whatever it has: psycopg2 gives a datetime, SQLite gives a string.
    Comparing a str against the expected datetimes would silently mark EVERY
    bucket missing, so normalise here.
    """
    if isinstance(v, datetime):
        return v
    if v is None:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None


def _stored_buckets(db, ticker, interval, range_start, range_end) -> set:
    rows = db.execute(text(
        "SELECT timestamp FROM candles WHERE ticker = :t AND timeframe = :tf "
        "AND timestamp >= :s AND timestamp <= :e"
    ), {"t": ticker, "tf": interval, "s": range_start, "e": range_end}).fetchall()
    return {d for d in (_as_dt(r[0]) for r in rows) if d is not None}


def _absent_buckets(db, ticker, interval, range_start, range_end) -> set:
    """Buckets already recorded as unavailable (see backfill_absent).

    Excluded from fetching so a proven-absent interval is never re-requested --
    that is what stops an illiquid ticker's no-trade buckets from re-triggering a
    repair on every chart open. The buckets are *recorded*, not dropped: the
    ledger carries the reason and the evidence.
    """
    try:
        rows = db.execute(text(
            "SELECT bucket FROM backfill_absent WHERE ticker = :t AND interval = :tf "
            "AND bucket >= :s AND bucket <= :e"
        ), {"t": ticker, "tf": interval, "s": range_start, "e": range_end}).fetchall()
        return {d for d in (_as_dt(r[0]) for r in rows) if d is not None}
    except Exception:
        return set()          # table absent (e.g. a bare test schema) -> nothing recorded


def _pre_data_floor(db, ticker, interval, range_start):
    """Earliest moment this ticker is known to have data.

    Uses a real listing date when the schema provides one. `stock_metadata` has
    NO listing_date column today (verified), so this falls back to the ticker's
    earliest stored candle -- which also covers retention: 5m keeps only ~60
    sessions, so anything older is intentionally gone rather than missing.

    Buckets before this floor are recorded as `pre_data`, never as
    `provider_absent`: with no listing metadata a pre-listing period cannot be
    told apart from a provider gap, so the honest reason is recorded instead of
    a guess.
    """
    try:
        row = db.execute(text(
            "SELECT listing_date FROM stock_metadata WHERE ticker = :t LIMIT 1"),
            {"t": ticker}).fetchone()
        if row and row[0]:
            ld = _as_dt(row[0])
            if ld is not None:
                if not isinstance(row[0], datetime):
                    ld = datetime(ld.year, ld.month, ld.day, SESSION_START[0], SESSION_START[1])
                return ld
    except Exception:
        pass                  # no listing_date column in this schema
    try:
        row = db.execute(text(
            "SELECT min(timestamp) FROM candles WHERE ticker = :t AND timeframe = :tf"),
            {"t": ticker, "tf": interval}).fetchone()
        if row and row[0]:
            return _as_dt(row[0])
    except Exception:
        pass
    return None


def missing_ranges(db, ticker, interval, range_start, range_end, now=None):
    """Exact missing ranges, merged into provider-sized requests.

    Returns (missing_buckets:set, ranges:list[(start,end)]). Buckets are merged
    only while the merged span stays inside the provider's per-request limit, so
    an older multi-week gap becomes one or more bounded requests -- never a
    substitute "today only" range.

    Buckets already proven unavailable (backfill_absent) are excluded, so they
    are neither re-fetched nor re-reported as missing.
    """
    expected = expected_buckets(range_start, range_end, now=now)
    stored = _stored_buckets(db, ticker, interval, range_start, range_end)
    absent = _absent_buckets(db, ticker, interval, range_start, range_end)
    missing = [b for b in expected if b not in stored and b not in absent]
    if not missing:
        return set(), []
    span = timedelta(days=MAX_SPAN_DAYS.get(interval, 60) - 1)
    ranges, start, prev = [], missing[0], missing[0]
    for b in missing[1:]:
        if (b - prev) == timedelta(minutes=5) and (b - start) <= span:
            prev = b
            continue
        ranges.append((start, prev + timedelta(minutes=5)))
        start = prev = b
    ranges.append((start, prev + timedelta(minutes=5)))

    # COALESCE near-adjacent ranges. An illiquid ticker can be missing thousands of
    # scattered buckets (RPSGVENT: 3,088 of them) which would otherwise become
    # ~100 tiny requests and exhaust the account quota on one symbol. Merging across
    # gaps shorter than COALESCE_GAP keeps the request count bounded -- and any
    # bucket the provider cannot supply simply stays missing and is re-reported by
    # the post-fetch verification, so nothing is ever marked complete incorrectly.
    merged = []
    for (s, e) in ranges:
        if merged and (s - merged[-1][1]) <= COALESCE_GAP and (e - merged[-1][0]) <= span:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return set(missing), merged


# ─────────────────────────────────────────────────────────────────────────────
# Job store
# ─────────────────────────────────────────────────────────────────────────────
def enqueue(ticker, interval, range_start, range_end, exchange="NSE", reason="gap"):
    """Idempotent: an identical open job is reused rather than duplicated.

    A `chart_open` job is PROMOTED to the front of the queue, because the user is
    looking at that ticker right now while everything behind it is background sweep
    work nobody is waiting on. `claim()` orders by `next_attempt_at` (NULLs fall back
    to created_at), so stamping the promoted job an hour into the past puts it ahead
    of the whole backlog without touching the claim query, the worker's pacing or the
    provider limiter.

    Measured on production before this: 136 eligible jobs at one job per 20s in
    market hours = ~45 minutes of waiting, which is why a ticker a user had just
    opened visibly backfilled "a few minutes later" (or only after switching
    timeframe and back) instead of on open. The promotion is applied on CONFLICT too:
    a ticker whose job is already queued/retry_wait/partially_complete for the same
    range is exactly the case that needs it.
    """
    ensure_table()
    t = (ticker or "").strip().upper().replace(".NS", "").replace(".BO", "")
    if not t or interval not in MAX_SPAN_DAYS:
        return None
    db = database.SessionLocal()
    try:
        row = db.execute(text("""
            INSERT INTO backfill_jobs (ticker, exchange, interval, range_start, range_end,
                                       status, next_attempt_at, last_error)
            VALUES (:t, :x, :i, :s, :e, 'queued',
                    CASE WHEN :r = 'chart_open' THEN NOW() - INTERVAL '1 hour' ELSE NOW() END,
                    :r)
            ON CONFLICT (ticker, exchange, interval, range_start, range_end)
              WHERE status IN ('queued','running','retry_wait','partially_complete')
            DO UPDATE SET updated_at = NOW(),
                          next_attempt_at = CASE
                              WHEN :r = 'chart_open' THEN NOW() - INTERVAL '1 hour'
                              ELSE backfill_jobs.next_attempt_at END
            RETURNING id, status
        """), {"t": t, "x": exchange, "i": interval, "s": range_start, "e": range_end,
               "r": reason}).fetchone()
        db.commit()
        return row[0] if row else None
    except Exception as e:
        db.rollback()
        print(f"[BackfillJob] enqueue {t} {interval} failed: {e}")
        return None
    finally:
        db.close()


def has_missing(db, ticker, interval, range_start, range_end, now=None) -> bool:
    """True when at least one expected bucket is genuinely missing.

    Gates job creation so a request that has nothing to repair never creates a
    job (measured: 8 jobs had already finished as no-ops, and holiday-extended
    weekends used to enqueue one per ticker).
    """
    try:
        missing, _ = missing_ranges(db, ticker, interval, range_start, range_end, now=now)
        return bool(missing)
    except Exception:
        return False


def has_open_job(ticker, interval) -> bool:
    """True when a repair for this ticker/interval is queued or in flight.

    Lets the chart report `backfilling` for a repair it did not itself enqueue,
    so the frontend's bounded re-poll merges the repaired candles in.
    """
    t = (ticker or "").strip().upper().replace(".NS", "").replace(".BO", "")
    if not t:
        return False
    try:
        ensure_table()
        db = database.SessionLocal()
        try:
            return db.execute(text(
                "SELECT 1 FROM backfill_jobs WHERE ticker = :t AND interval = :i "
                "AND status IN ('queued','running','retry_wait','partially_complete') LIMIT 1"
            ), {"t": t, "i": interval}).fetchone() is not None
        finally:
            db.close()
    except Exception:
        return False


def mark_absent(ticker, exchange, interval, buckets, reason, evidence=""):
    """Record buckets that cannot be repaired, so they are never re-fetched.

    `reason` is 'provider_absent' (proven by a bracketing successful response) or
    'pre_data' (before any observed data / listing date). Idempotent.
    """
    buckets = list(buckets or [])
    if not buckets:
        return 0
    try:
        ensure_table()
        db = database.SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO backfill_absent (ticker, exchange, interval, bucket, reason, evidence)
                VALUES (:t, :x, :i, :b, :r, :ev)
                ON CONFLICT (ticker, exchange, interval, bucket) DO NOTHING
            """), [{"t": ticker, "x": exchange, "i": interval, "b": b,
                    "r": reason, "ev": (evidence or "")[:200]} for b in buckets])
            db.commit()
            return len(buckets)
        finally:
            db.close()
    except Exception as e:
        print(f"[BackfillJob] mark_absent({ticker} {interval} {reason}) failed: {e}")
        return 0


def claim(worker_id, limit=1):
    """Atomically lease up to `limit` due jobs. Safe across workers/restarts:
    a job is only claimable when queued, or when its lease has expired."""
    ensure_table()
    db = database.SessionLocal()
    try:
        rows = db.execute(text("""
            WITH due AS (
                SELECT id FROM backfill_jobs
                WHERE (status = 'queued' OR status = 'retry_wait' OR status = 'partially_complete'
                       OR (status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at < NOW())))
                  AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                ORDER BY COALESCE(next_attempt_at, created_at) ASC
                LIMIT :n
                FOR UPDATE SKIP LOCKED
            )
            UPDATE backfill_jobs j
               SET status = 'running', claimed_by = :w, last_attempt_at = NOW(),
                   attempts = j.attempts + 1,
                   lease_expires_at = NOW() + make_interval(secs => :lease),
                   updated_at = NOW()
              FROM due
             WHERE j.id = due.id
            RETURNING j.id, j.ticker, j.exchange, j.interval, j.range_start, j.range_end, j.attempts
        """), {"w": worker_id, "n": limit, "lease": JOB_LEASE_SEC}).fetchall()
        db.commit()
        return [dict(id=r[0], ticker=r[1], exchange=r[2], interval=r[3],
                     range_start=r[4], range_end=r[5], attempts=r[6]) for r in rows]
    except Exception as e:
        db.rollback()
        print(f"[BackfillJob] claim failed: {e}")
        return []
    finally:
        db.close()


def _backoff_seconds(attempts, retry_after=0.0):
    base = min(JOB_BASE_BACKOFF_SEC * (2 ** max(0, attempts - 1)), JOB_MAX_BACKOFF_SEC)
    jittered = base * (0.5 + random.random() * 0.5)
    return max(jittered, retry_after)


def finish(job_id, status, *, fetched=0, persisted=0, remaining=0, expected=None,
           error=None, failure_kind=None, retry_after=0.0, attempts=1,
           backoff_override=None):
    db = database.SessionLocal()
    try:
        nxt = None
        if status in ("retry_wait", "partially_complete"):
            delay = (backoff_override if backoff_override is not None
                     else _backoff_seconds(attempts, retry_after))
            nxt = datetime.now() + timedelta(seconds=max(0.0, delay))
        db.execute(text("""
            UPDATE backfill_jobs
               SET status = :st, fetched_count = :f, persisted_count = :p,
                   remaining_count = :rm, expected_count = COALESCE(:ex, expected_count),
                   last_error = :err, failure_kind = :fk, next_attempt_at = :nxt,
                   lease_expires_at = NULL, updated_at = NOW()
             WHERE id = :id
        """), {"st": status, "f": fetched, "p": persisted, "rm": remaining, "ex": expected,
               "err": (error or "")[:400] or None, "fk": failure_kind, "nxt": nxt,
               "id": job_id})
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[BackfillJob] finish {job_id} failed: {e}")
    finally:
        db.close()


def cleanup(days=None):
    """Retention: terminal jobs are not kept forever."""
    ensure_table()
    days = days or JOB_RETENTION_DAYS
    db = database.SessionLocal()
    try:
        r = db.execute(text(
            "DELETE FROM backfill_jobs "
            "WHERE status IN ('complete','complete_with_gaps','permanently_failed','superseded') "
            "AND updated_at < NOW() - make_interval(days => :d)"), {"d": days})
        db.commit()
        return r.rowcount or 0
    except Exception as e:
        db.rollback()
        return 0
    finally:
        db.close()


def stats():
    ensure_table()
    db = database.SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT status, count(*) FROM backfill_jobs GROUP BY status ORDER BY status")).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Executing a job: fetch exactly the missing ranges, persist, then verify
# ─────────────────────────────────────────────────────────────────────────────
_ANGEL_INTERVAL = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
                   "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "1D": "ONE_DAY"}


def run_job(job):
    """Returns (status, detail).

    Terminal outcomes are distinguished by EVIDENCE, never guessed:

      complete            -- verified: every expected bucket is now stored.
      complete_with_gaps  -- finished; specific buckets recorded as unavailable.
                             The reason is explicit: `provider_absent` (a
                             successful response bracketed the bucket and omitted
                             it) or `pre_data` (predates any observed data).
      permanently_failed  -- unsupported/invalid symbol (not retryable).
      retry_wait          -- throttled or transient provider error; bounded backoff.
      partially_complete  -- the response was partial; some buckets were persisted.

    An interval is NEVER called provider_absent merely because attempts returned
    nothing: it requires a fetch that SUCCEEDED and whose returned span brackets
    the bucket (data before AND after) while omitting it, which rules out a
    truncated range.
    """
    import historical_service as hs
    from historical_service import HistoricalFetchError

    # 5m is the ONLY intraday source of truth. A job for a derived timeframe
    # (15m/30m/1h/4h) must never spend a provider request -- that data is built
    # from stored 5m on the fly by CandleResampler and is deliberately not stored.
    # Such jobs are marked `superseded`: terminal, honest, and no quota wasted.
    if job["interval"] != "5m":
        finish(job["id"], "superseded", fetched=0, persisted=0, remaining=0,
               failure_kind="derived_from_5m")
        return "superseded", {"interval": job["interval"], "derived_from": "5m"}

    db = database.SessionLocal()
    try:
        missing, ranges = missing_ranges(db, job["ticker"], job["interval"],
                                        job["range_start"], job["range_end"])
        floor = _pre_data_floor(db, job["ticker"], job["interval"], job["range_start"])
    finally:
        db.close()

    expected_n = len(missing)
    if not missing:
        finish(job["id"], "complete", fetched=0, persisted=0, remaining=0,
               expected=0, failure_kind=None)
        return "complete", {"expected": 0, "persisted": 0, "remaining": 0}

    # Buckets before any observed data are `pre_data`, deliberately NOT
    # provider_absent -- with no listing metadata they cannot be told apart.
    pre_data = {b for b in missing if floor is not None and b < floor}

    # Nothing the provider could plausibly supply -> do not spend a request.
    if pre_data and len(pre_data) == expected_n:
        mark_absent(job["ticker"], job["exchange"], job["interval"], pre_data,
                    "pre_data", "before earliest observed data %s" % floor)
        finish(job["id"], "complete_with_gaps", fetched=0, persisted=0,
               remaining=expected_n, expected=expected_n,
               error="all missing buckets predate any observed data for this ticker",
               failure_kind="pre_data")
        return "complete_with_gaps", {"remaining": expected_n, "reason": "pre_data"}

    # Respect the shared cooldown before spending a request, but come back FAST:
    # a long exponential backoff here is what starved TATACAP's real 8-session hole.
    _cd = hs.rate_limit_backoff_remaining()
    if _cd > 0:
        finish(job["id"], "retry_wait", expected=expected_n, remaining=expected_n,
               error="shared provider cooldown active", failure_kind="throttled",
               retry_after=_cd, attempts=job["attempts"],
               backoff_override=max(float(THROTTLE_RETRY_SEC), _cd + 5.0))
        return "retry_wait", {"reason": "cooldown"}

    fetched = 0
    last_err = None
    kind = None
    retry_after = 0.0
    unsupported = False
    # (range_start, range_end, min_returned_ts, max_returned_ts, call_succeeded)
    served = []
    svc = hs.historical_service
    for (r_start, r_end) in ranges:
        candles = None
        # Auth failures are retried INLINE against the same range: the session is
        # refreshed and the fetch repeated, so a stale token never consumes the
        # attempt budget. Measured before this change: 180 perfectly valid jobs were
        # permanently failed after 6 attempts that were all "Invalid Token".
        for _auth_try in range(3):
            try:
                if not svc.is_logged_in:
                    svc.login()
                candles = svc.get_historical_candles(
                    ticker=job["ticker"], exchange=job["exchange"],
                    interval=_ANGEL_INTERVAL[job["interval"]],
                    from_date=r_start, to_date=r_end, raise_on_error=True) or []
                break
            except HistoricalFetchError as e:
                last_err = str(e)
                if hs._looks_auth_error(e):
                    kind = "auth"
                    svc.is_logged_in = False
                    try:
                        svc.login()
                    except Exception:
                        pass
                    time.sleep(0.5)
                    continue
                if not e.retryable:
                    unsupported, kind = True, "unsupported"      # invalid/unknown symbol
                else:
                    kind = "throttled" if hs._looks_rate_limited(e) else "transient"
                retry_after = max(retry_after, hs._retry_after_seconds(e))
                served.append((r_start, r_end, None, None, False))
                candles = None
                break
            except Exception as e:
                last_err, kind = str(e), "transient"
                served.append((r_start, r_end, None, None, False))
                candles = None
                break
        if candles is None:
            break
        fetched += len(candles)
        if candles:
            ts = [_ist_naive(c["timestamp"]) for c in candles]
            served.append((r_start, r_end, min(ts), max(ts), True))
            try:
                from aggregator import batch_flush_candles
                batch_flush_candles([{
                    "ticker": job["ticker"], "timeframe": job["interval"],
                    "timestamp": c["timestamp"], "open": c["open"], "high": c["high"],
                    "low": c["low"], "close": c["close"], "volume": c["volume"],
                    "is_completed": True, "data_source": "ANGELONE", "is_backfilled": True,
                } for c in candles])
            except Exception as e:
                last_err, kind = f"persist failed: {e}", "persist"
                break
        else:
            # a successful response that genuinely contained nothing for the range
            served.append((r_start, r_end, None, None, True))

    # ── verification: re-read what is ACTUALLY stored for the requested range ──
    db = database.SessionLocal()
    try:
        still_missing, _ = missing_ranges(db, job["ticker"], job["interval"],
                                          job["range_start"], job["range_end"])
    finally:
        db.close()
    persisted = expected_n - len(still_missing)

    if not still_missing:
        finish(job["id"], "complete", fetched=fetched, persisted=persisted,
               remaining=0, expected=expected_n)
        return "complete", {"expected": expected_n, "fetched": fetched, "persisted": persisted}

    if unsupported:
        finish(job["id"], "permanently_failed", fetched=fetched, persisted=persisted,
               remaining=len(still_missing), expected=expected_n,
               error=last_err or "unsupported symbol", failure_kind="unsupported")
        return "permanently_failed", {"remaining": len(still_missing), "error": last_err}

    # provider_absent needs EVIDENCE: the call succeeded and its returned span
    # brackets the bucket (a candle before AND after it) while omitting it.
    bracketed = set()
    for b in still_missing:
        if b in pre_data:
            continue
        for (s, e, lo, hi, ok) in served:
            if ok and lo is not None and s <= b <= e and lo < b < hi:
                bracketed.add(b)
                break
    if bracketed:
        mark_absent(job["ticker"], job["exchange"], job["interval"], bracketed,
                    "provider_absent",
                    "successful response returned data before and after the bucket without returning it")
    if pre_data:
        mark_absent(job["ticker"], job["exchange"], job["interval"], pre_data,
                    "pre_data", "before earliest observed data %s" % floor)

    unresolved = {b for b in still_missing if b not in bracketed and b not in pre_data}
    if not unresolved:
        finish(job["id"], "complete_with_gaps", fetched=fetched, persisted=persisted,
               remaining=len(still_missing), expected=expected_n,
               error="%d interval(s) recorded as unavailable" % len(bracketed | pre_data),
               failure_kind="provider_absent" if bracketed else "pre_data")
        return "complete_with_gaps", {"remaining": len(still_missing),
                                      "provider_absent": len(bracketed),
                                      "pre_data": len(pre_data)}

    # Bounded end: a job that has spent its attempts without closing the gap stops
    # honestly instead of looping. If the provider ANSWERED SUCCESSFULLY and simply
    # had no candle for the remaining buckets, the terminal state is
    # `complete_with_gaps` / `provider_empty` -- reported as a gap, never as
    # complete, and deliberately NOT as provider_absent (absence needs bracketing
    # evidence, above). Only a genuine error stays `permanently_failed`.
    if job["attempts"] >= JOB_MAX_ATTEMPTS and kind != "auth":
        if persisted > 0:
            finish(job["id"], "complete_with_gaps", fetched=fetched, persisted=persisted,
                   remaining=len(still_missing), expected=expected_n,
                   error="provider returned no candle for %d interval(s) after %d attempts"
                         % (len(still_missing), job["attempts"]),
                   failure_kind="provider_empty")
            return "complete_with_gaps", {"remaining": len(still_missing),
                                          "reason": "provider_empty"}
        # NOTHING was persisted for the whole window. An empty or failed provider
        # response is NOT evidence that the data does not exist: DRREDDY sat at
        # provider_empty with 38 buckets unfilled after 7 attempts and was then
        # abandoned forever, so opening the ticker could never fill the gap. Keep it
        # retryable at the capped backoff instead. Provider load stays bounded --
        # one attempt per JOB_MAX_BACKOFF_SEC per job -- and a genuinely dead symbol
        # is still retired by the unsupported-symbol path above.
        finish(job["id"], "retry_wait", fetched=fetched, persisted=0,
               remaining=len(still_missing), expected=expected_n,
               error=last_err or "provider returned no data (kept retryable)",
               failure_kind=kind or "empty_response",
               backoff_override=JOB_MAX_BACKOFF_SEC)
        return "retry_wait", {"remaining": len(still_missing),
                              "reason": "provider_empty_retry"}

    if persisted > 0:
        finish(job["id"], "partially_complete", fetched=fetched, persisted=persisted,
               remaining=len(still_missing), expected=expected_n,
               error=last_err or "provider returned no data for part of the range",
               failure_kind=kind or "empty_response", retry_after=retry_after,
               attempts=job["attempts"])
        return "partially_complete", {"remaining": len(still_missing), "persisted": persisted}

    finish(job["id"], "retry_wait", fetched=fetched, persisted=0,
           remaining=len(still_missing), expected=expected_n,
           error=last_err or "no candles returned", failure_kind=kind or "empty_response",
           retry_after=retry_after, attempts=job["attempts"])
    return "retry_wait", {"remaining": len(still_missing), "error": last_err}


STALE_LEASE_MINUTES = int(os.getenv("BACKFILL_STALE_LEASE_MINUTES", "10"))


def reap_stale(max_age_min=None):
    """Return jobs that have been stuck in `running` to the retry queue.

    A claim sets `running`; if the process dies or a fetch hangs, nothing ever moves
    that row again and a single stuck job (observed: BAFNAPH, `running` for 40
    minutes) blocks every other repair for that (ticker, interval) because enqueue
    refuses while a job is running. Reaping is a bounded UPDATE: one row per stuck
    job, moved to retry_wait with the normal short backoff -- never a bulk rewrite of
    finished work.
    """
    age = int(max_age_min if max_age_min is not None else STALE_LEASE_MINUTES)
    db = database.SessionLocal()
    try:
        res = db.execute(text("""
            UPDATE backfill_jobs
               SET status = 'retry_wait',
                   lease_expires_at = NULL,
                   next_attempt_at = NOW() + INTERVAL '120 seconds',
                   failure_kind = 'stale_lease',
                   last_error = 'reclaimed: stuck in running past the lease window',
                   updated_at = NOW()
             WHERE status = 'running'
               AND (last_attempt_at IS NULL
                    OR last_attempt_at < NOW() - make_interval(mins => :age))
        """), {"age": age})
        db.commit()
        n = res.rowcount or 0
        if n:
            print(f"[BackfillJob] reclaimed {n} stale running job(s) older than {age} min")
        return n
    except Exception as e:
        db.rollback()
        print(f"[BackfillJob] reap_stale failed: {e}")
        return 0
    finally:
        db.close()


def run_batch(limit=3, worker_id=None):
    """Claim and execute a bounded batch. Returns a small result summary."""
    worker_id = worker_id or f"w{os.getpid()}"
    try:
        reap_stale()
    except Exception:
        pass
    out = []
    for job in claim(worker_id, limit=limit):
        try:
            status, detail = run_job(job)
        except Exception as e:
            import traceback
            status, detail = "error", {"error": str(e)}
            print(f"[BackfillJob] {job['ticker']} {job['interval']} INTERNAL ERROR:\n{traceback.format_exc()}")
            finish(job["id"], "retry_wait", error=str(e), failure_kind="internal",
                   attempts=job["attempts"])
        out.append((job["ticker"], job["interval"], status, detail))
        print(f"[BackfillJob] {job['ticker']} {job['interval']} -> {status} {detail}")
    return out
