# ADR-002: Live Candle Architecture

Status: Accepted

## Context

The charting system must deliver NSE-session-aligned candles for many tickers in
real time. AngelOne WebSocket provides ~200ms ticks. Pandas `resample()` with
default bucket alignment (09:00) is wrong for NSE, which opens at 09:15 IST.

Verified facts:
- `aggregator.py::Live5mBuilder` (`candle_aggregator` singleton, aggregator.py:757)
  builds **only 5m candles** in memory (`SNAPSHOT_TFS = ["5m"]`), flushed to the
  `candles` table as completed rows via `batch_flush_candles` (aggregator.py:560).
- 15m/30m/1h/1D/1W/1M are produced by resampling on demand
  (`CandleResampler.resample_5m_to`, resampler.py:30) or by `RetentionService`
  (ADR-007), not built tick-by-tick.
- Bucket alignment is NSE-anchored: 09:15 is a valid 15m boundary (555 % 15 = 0),
  but 30m/1h/4h need `offset='15min'` (555 % 30 = 15) so the first bucket is 09:15,
  not 09:00 (resampler.py:59-73).
- Aggregator staleness guards: suspicious ts (< 1.5e9) and frozen ts
  (>30s old) fall back to server time; stale out-of-order ticks are skipped
  (aggregator.py:211-230). Forming 5m candles are flushed on market close and on
  new-bucket transitions.

## Decision

- Build only 5m candles live from ticks; treat all higher timeframes as derived
  data (resample on read or by retention, never build them tick-by-tick).
- Always use NSE 09:15-anchored buckets when resampling to 30m/1h/4h via the
  `offset='15min'` mechanism.
- Store completed candles in the `candles` table with `is_completed=True`,
  upserted with `ON CONFLICT (ticker, timeframe, timestamp)` preserving the
  first `open` and keeping high/low as greatest/least (aggregator.py:590-600).

## Alternatives considered

- **Tick-by-tick building of all timeframes**: rejected. 6× write load and
  duplicate aggregation paths; derived-on-read is simpler and consistent.
- **Default pandas 09:00-aligned buckets**: rejected. Produces 09:00-labeled
  candles that don't match NSE sessions (this was the cause of
  `test_resample_to_30m` failing — the test still assumes 09:00 buckets).

## Consequences

- 1m candles are **not** written to the `candles` table by any runtime module.
  The execution engine's 1m price query (ADR-004) therefore always returns empty.
- Forming 5m state is lost on restart; Recovery Service (ADR-003) must rebuild it.
- The aggregate `get_current()` only exposes the forming 5m candle; consumers
  needing sub-5s freshness should read `angelone_service.latest_ticks` instead.

## Rollback considerations

If the 09:15 offset is removed, all 30m/1h/4h candles relabel to 09:00 boundaries
— data is not corrupt, but frontend charts will misalign with the session.
Keeping the offset is the safe direction.

## Related files

- `backend/aggregator.py` — `Live5mBuilder`, `process_tick`, `batch_flush_candles`
- `backend/resampler.py` — `CandleResampler` NSE offset logic
- `backend/models.py::Candle` — `candles` table, `uix_candle_key`
- `backend/tests/test_resampler.py` — stale 09:00-bucket expectation
