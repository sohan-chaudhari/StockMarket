# Chain B unit decision (superseding the NSE_SESSIONS_PER_YEAR approximation
# used in an earlier revision of this file): RetentionService._compute_cutoff
# has exactly two cutoff branches —
#   `after_trading_sessions` (truthy check): counts real NSE trading days
#      backward via _session_cutoff(), skipping weekends/holidays.
#   `after_days` (the fallback when after_trading_sessions is falsy/omitted):
#      pure calendar-day subtraction, `(now - timedelta(days=N))`, truncated
#      to midnight. No session-skipping at all.
# "2 years" and "5 years" are calendar-time concepts, not trading-session
# counts — using after_trading_sessions with an assumed sessions/year
# constant was an unnecessary approximation once the actually-correct native
# unit (after_days) was checked and confirmed reliable: same `now` reference
# (ist_now_naive(), naive IST, identical to the session-based branch — no
# timezone discrepancy between the two), already exercised by the same
# atomic/checksum-verified/idempotent downgrade machinery, zero code changes
# required. Chain B now uses after_days=730 (2yr) / 1825 (5yr) directly.
#
# IMPORTANT: `after_trading_sessions` MUST be omitted (not 0, not set) on any
# rule meant to use after_days — `_compute_cutoff` checks truthiness, so a
# populated after_trading_sessions always wins over after_days even if both
# are present. Chain A's rules keep using after_trading_sessions as before;
# only Chain B's two rules changed.

RETENTION_POLICY = {
    # Two INDEPENDENT retention chains, both using the generic session-based
    # RetentionRule/RetentionService engine (see retention_service.py) — no
    # chain-specific code exists; only the policy entries below differ.
    #
    # Chain A — Intraday pyramid (progressive NSE trading sessions):
    #   5m  → retain newest 60 sessions            (0–60)
    #   15m → retain sessions 60–120             (compressed from 5m older than 60)
    #   30m → retain sessions 120–180            (compressed from 15m older than 120)
    #   1h  → retain sessions 180–365            (compressed from 30m older than 180)
    #   4h  → retain sessions 365+               (compressed from 1h older than 365)
    #
    #   4h is the FINAL tier of Chain A. There is intentionally NO 4h→1D rule —
    #   verified and re-confirmed by explicit approval, twice now: daily/
    #   weekly/monthly candles are a SEPARATE chain, never derived from
    #   intraday retention.
    #
    # Chain B — Daily+ pyramid (independent of Chain A, never crosses into it):
    #   1D → retain newest 730 CALENDAR days (2yr), compress older 1D into 1W.
    #   1W → retain newest 1825 CALENDAR days (5yr), compress older 1W into 1M.
    #   1M → terminal. NO further compression rule exists for 1M as a source —
    #        "do not compress further" is expressed by the simple absence of a
    #        1M-sourced rule, not a special case in the engine. Long-term
    #        monthly history is therefore retained indefinitely by design
    #        (its storage footprint is tiny relative to intraday data — see
    #        the storage analysis in the accompanying report). No deletion or
    #        further compression of 1M was requested or implemented.
    #
    #   Chain B's cutoffs run through the SAME RetentionService._compute_cutoff
    #   machinery Chain A uses — same continuous rolling-window behavior (the
    #   window is recomputed fresh every cycle, never a one-time cutover),
    #   same live-window protection, same checksum-verified atomic downgrade,
    #   same naive-IST `now` reference. Only the cutoff UNIT differs (calendar
    #   days here vs. trading sessions in Chain A) — no new retention
    #   mechanism was introduced for this chain.
    "version": 6,
    "retention_policy": [
        {"source_tf": "5m",  "target_tf": "15m", "after_trading_sessions": 60,  "batch_size": 100},
        {"source_tf": "15m", "target_tf": "30m", "after_trading_sessions": 120, "batch_size": 100},
        {"source_tf": "30m", "target_tf": "1h",  "after_trading_sessions": 180, "batch_size": 100},
        {"source_tf": "1h",  "target_tf": "4h",  "after_trading_sessions": 365, "batch_size": 100},
        {"source_tf": "1D",  "target_tf": "1W",  "after_days": 730,  "batch_size": 100},
        {"source_tf": "1W",  "target_tf": "1M",  "after_days": 1825, "batch_size": 100},
    ],
}