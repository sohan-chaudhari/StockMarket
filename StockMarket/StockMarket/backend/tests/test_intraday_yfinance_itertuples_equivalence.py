"""Tests for the STARTUP BACKFILL CPU/GIL HOTSPOT INVESTIGATION phase's Step 3
change: main.py's _fetch_yfinance_intraday used
`for idx, row in yf_int.iterrows(): ...` to turn a yfinance intraday OHLCV
DataFrame into a list of _YfCandle objects -- boxing every row into a fresh
pandas Series is expensive, and this exact code path is exercised by
_startup_backfill (an always-on daemon thread starting at boot, ~56 tickers
x 2 intervals) as part of the residual event-loop-lag investigation.

The loop body was extracted verbatim into a module-level pure function,
main._yf_intraday_df_to_candles(yf_int, ticker, interval), and rewritten to
use yf_int.itertuples(index=True) instead of iterrows().

These tests prove itertuples() output is IDENTICAL to the old iterrows()
implementation by keeping a byte-for-byte copy of the ORIGINAL logic here
as a reference oracle and comparing both against the same synthetic
DataFrames -- not "does the output look reasonable" but "does the new code
produce exactly what the old code produced," per this phase's explicit
instruction to verify equivalence before optimizing.
"""
import unittest
from datetime import datetime, timezone

import pandas as pd

from main import _yf_intraday_df_to_candles, _safe_float, _safe_int, IST


def _reference_iterrows_implementation(yf_int, clean_ticker, interval):
    """Verbatim copy of main.py's PRE-CHANGE inline loop inside
    _fetch_yfinance_intraday (the code this phase replaced) -- the
    equivalence oracle every test below compares against. Do not "fix" or
    "improve" this copy; it must stay exactly what the old code did, or it
    stops being a valid oracle."""
    from aggregator import snap_to_nse_session, fix_ohlc
    bucket_min = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}.get(interval, 5)
    intraday_candles_5min = []
    seen_ts = set()
    for idx, row in yf_int.iterrows():
        try:
            ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if hasattr(idx, 'tz') and idx.tz is not None:
                ts = ts.astimezone(IST).replace(tzinfo=None)
            else:
                ts = ts.replace(tzinfo=timezone.utc).astimezone(IST).replace(tzinfo=None)
            epoch = int(ts.replace(tzinfo=IST).timestamp())
            snapped_epoch = snap_to_nse_session(epoch, bucket_min)
            from main import _epoch_to_ist_dt
            snapped_dt = _epoch_to_ist_dt(snapped_epoch)
            o = _safe_float(row.get('Open'))
            h = _safe_float(row.get('High'))
            l = _safe_float(row.get('Low'))
            c = _safe_float(row.get('Close'))
            v = _safe_int(row.get('Volume'))
            if o is None and h is None and l is None and c is None:
                continue
            o = o or 0.0; h = h or 0.0; l = l or 0.0; c = c or 0.0
            o, h, l, c = fix_ohlc(o, h, l, c)
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                continue
            ts_key = snapped_dt.strftime("%Y%m%d%H%M")
            if ts_key in seen_ts:
                continue
            seen_ts.add(ts_key)
            intraday_candles_5min.append({
                "ticker": clean_ticker, "timeframe": interval, "timestamp": snapped_dt,
                "open": o, "high": h, "low": l, "close": c, "volume": v, "is_completed": True,
            })
        except Exception:
            pass
    return intraday_candles_5min


def _candle_to_dict(c):
    return {"ticker": c.ticker, "timeframe": c.timeframe, "timestamp": c.timestamp,
            "open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": c.volume, "is_completed": c.is_completed}


def _make_df(rows, tz=None):
    """Builds a yfinance-shaped intraday OHLCV DataFrame: a DatetimeIndex
    (IST market-hours timestamps by default) plus O/H/L/C/V columns."""
    index = pd.to_datetime([r["ts"] for r in rows])
    if tz:
        index = index.tz_localize(tz)
    return pd.DataFrame({
        "Open": [r["o"] for r in rows],
        "High": [r["h"] for r in rows],
        "Low": [r["l"] for r in rows],
        "Close": [r["c"] for r in rows],
        "Volume": [r["v"] for r in rows],
    }, index=index)


# A normal NSE trading-session window (10:00/10:05/10:10 IST on a weekday).
# Expressed tz-naive, as yfinance's index commonly is for this code path --
# the un-changed, pre-existing code treats a tz-naive index as UTC (the
# `else` branch: `ts.replace(tzinfo=timezone.utc).astimezone(IST)`), so
# these constants are written in UTC (IST minus 5h30m) so they land in
# mid-session IST once converted, in three DISTINCT 5-minute buckets.
_T0 = "2026-01-05 04:30:00"   # -> 10:00 IST
_T1 = "2026-01-05 04:35:00"   # -> 10:05 IST
_T2 = "2026-01-05 04:40:00"   # -> 10:10 IST


class EquivalenceAgainstReferenceOracle(unittest.TestCase):
    def _assert_equivalent(self, df, ticker="RELIANCE", interval="5m"):
        expected = _reference_iterrows_implementation(df.copy(deep=True), ticker, interval)
        actual_objs = _yf_intraday_df_to_candles(df.copy(deep=True), ticker, interval)
        actual = [_candle_to_dict(c) for c in actual_objs]
        self.assertEqual(actual, expected)
        return actual

    def test_normal_multi_row_dataframe(self):
        df = _make_df([
            {"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 15000},
            {"ts": _T1, "o": 103.5, "h": 108.0, "l": 102.0, "c": 107.0, "v": 22000},
            {"ts": _T2, "o": 107.0, "h": 107.5, "l": 104.0, "c": 105.0, "v": 18000},
        ])
        result = self._assert_equivalent(df)
        self.assertEqual(len(result), 3)

    def test_empty_dataframe_returns_empty_list(self):
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        expected = _reference_iterrows_implementation(df.copy(), "INFY", "5m")
        actual = _yf_intraday_df_to_candles(df.copy(), "INFY", "5m")
        self.assertEqual(actual, [])
        self.assertEqual(expected, [])

    def test_single_row_dataframe(self):
        df = _make_df([{"ts": _T0, "o": 50.0, "h": 51.0, "l": 49.5, "c": 50.5, "v": 9000}])
        self._assert_equivalent(df)

    def test_zero_close_row_is_excluded_by_both(self):
        df = _make_df([
            {"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000},
            {"ts": _T1, "o": 0.0, "h": 0.0, "l": 0.0, "c": 0.0, "v": 0},
        ])
        result = self._assert_equivalent(df)
        self.assertEqual(len(result), 1)

    def test_nan_ohlc_normalized_to_zero_and_excluded_by_both(self):
        """Unlike the daily-prefill path's `x or 0` quirk, _safe_float
        explicitly converts NaN to its default (0.0) -- so a present-but-NaN
        Close becomes 0.0 here (not NaN), then fails the `c <= 0` guard and
        is excluded. Both implementations must agree on this."""
        df = _make_df([{"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": float("nan"), "v": 1000}])
        result = self._assert_equivalent(df)
        self.assertEqual(result, [])

    def test_nan_volume_normalized_to_zero_by_both(self):
        """_safe_int catches int(nan)'s ValueError and returns its default
        (0) -- no exception should propagate, unlike the daily-prefill
        path's raw int(NaN or 0)."""
        df = _make_df([{"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": float("nan")}])
        result = self._assert_equivalent(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volume"], 0)

    def test_missing_column_falls_back_to_default_identically(self):
        """No Volume column at all: Series.get('Volume') returns None
        (matching getattr(t, 'Volume', None) on the itertuples namedtuple)
        -- both feed into _safe_int(None) -> 0."""
        df = pd.DataFrame({
            "Open": [100.0], "High": [105.0], "Low": [98.0], "Close": [103.0],
        }, index=pd.to_datetime([_T0]))
        result = self._assert_equivalent(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volume"], 0)

    def test_negative_low_gets_fixed_by_fix_ohlc_identically(self):
        df = _make_df([{"ts": _T0, "o": 100.0, "h": 95.0, "l": -5.0, "c": 98.0, "v": 500}])
        result = self._assert_equivalent(df)
        # fix_ohlc: fixed_l = min(l, o, c) = min(-5, 100, 98) = -5 -- still
        # negative, so the o/h/l/c <= 0 guard excludes this row either way.
        self.assertEqual(result, [])

    def test_deduplication_by_snapped_bucket_identical(self):
        """Two raw rows landing in the SAME 5m-snapped bucket must dedupe
        to one candle (first-wins) in both implementations."""
        df = _make_df([
            {"ts": "2026-01-05 04:30:00", "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 1000},  # -> 10:00:00 IST
            {"ts": "2026-01-05 04:30:30", "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.5, "v": 1200},  # -> 10:00:30 IST, same 5m bucket
        ])
        result = self._assert_equivalent(df)
        self.assertEqual(len(result), 1)
        # First row wins (matches iterrows()'s in-order seen_ts dedup).
        self.assertAlmostEqual(result[0]["open"], 100.0, places=2)

    def test_timezone_aware_index_normalized_identically(self):
        df = _make_df([{"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000}], tz="UTC")
        result = self._assert_equivalent(df)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["timestamp"].tzinfo)

    def test_timezone_naive_index_normalized_identically(self):
        df = _make_df([{"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000}])
        result = self._assert_equivalent(df)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["timestamp"].tzinfo)

    def test_multiple_intervals_use_correct_bucket_size(self):
        df = _make_df([
            {"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000},
            {"ts": _T1, "o": 103.0, "h": 106.0, "l": 101.0, "c": 104.0, "v": 1100},
        ])
        for interval in ("1m", "5m", "15m", "30m", "1h"):
            self._assert_equivalent(df.copy(), interval=interval)

    def test_ticker_identity_and_ordering_preserved(self):
        df = _make_df([
            {"ts": _T0, "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000},
            {"ts": _T1, "o": 103.0, "h": 106.0, "l": 101.0, "c": 104.0, "v": 1100},
            {"ts": _T2, "o": 104.0, "h": 107.0, "l": 103.0, "c": 105.5, "v": 1200},
        ])
        result = self._assert_equivalent(df, ticker="TCS")
        self.assertTrue(all(c["ticker"] == "TCS" for c in result))
        self.assertTrue(all(c["timeframe"] == "5m" for c in result))
        # Ascending timestamp order preserved (same iteration order as input).
        self.assertEqual([c["timestamp"] for c in result], sorted(c["timestamp"] for c in result))


if __name__ == "__main__":
    unittest.main()
