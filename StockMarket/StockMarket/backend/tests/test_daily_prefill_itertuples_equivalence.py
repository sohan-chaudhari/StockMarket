"""Tests for the EVENT LOOP LAG FIX (Category B) phase's Step C change:
main.py's _fetch_daily (inside _daily_prefill_all, the daily 1D prefill
launched at startup) used to build its candle-row list with
`for idx, row in df.iterrows(): ...` -- boxing every row into a fresh
pandas Series is expensive and, since this closure runs inside a 3-worker
ThreadPoolExecutor at startup, competes for the GIL with the event loop for
longer than necessary. The loop body was extracted verbatim into a
module-level pure function, main._yf_daily_df_to_candle_rows(df, ticker),
and rewritten to use df.itertuples() instead.

These tests prove itertuples() output is IDENTICAL to the old iterrows()
implementation -- including its existing quirks -- by keeping a byte-for-
byte copy of the ORIGINAL iterrows() logic here as a reference oracle and
comparing both implementations against the same synthetic DataFrames. This
is deliberately NOT "does the output look reasonable" -- it is "does the
new code produce what the OLD code would have produced," per this phase's
explicit instruction not to blindly optimize without verifying semantic
equivalence.
"""
import unittest

import pandas as pd

from main import _yf_daily_df_to_candle_rows


def _reference_iterrows_implementation(df, tkr):
    """Verbatim copy of main.py's PRE-CHANGE inline loop (the code this
    phase replaced) -- the equivalence oracle every test below compares
    against. Do not "fix" or "improve" this copy; it must stay exactly what
    the old code did, bugs and all, or it stops being a valid oracle."""
    rows = []
    if df is None or df.empty:
        return rows
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
        ts_naive = ts.replace(tzinfo=None) if (hasattr(ts, 'tzinfo') and ts.tzinfo) else ts
        o = float(row.get('Open', 0) or 0)
        h = float(row.get('High', 0) or 0)
        l = float(row.get('Low', 0) or 0)
        c = float(row.get('Close', 0) or 0)
        v = int(row.get('Volume', 0) or 0)
        if o > 0 and h > 0 and l > 0 and c > 0:
            rows.append({"t": tkr, "ts": ts_naive,
                         "o": o, "h": max(o, h, c), "l": min(o, l, c),
                         "c": c, "v": v, "src": "YFINANCE"})
    return rows


def _make_df(rows, tz=None):
    """Builds a yfinance-shaped daily OHLCV DataFrame: a DatetimeIndex plus
    Open/High/Low/Close/Volume columns."""
    index = pd.to_datetime([r["date"] for r in rows])
    if tz:
        index = index.tz_localize(tz)
    return pd.DataFrame({
        "Open": [r["o"] for r in rows],
        "High": [r["h"] for r in rows],
        "Low": [r["l"] for r in rows],
        "Close": [r["c"] for r in rows],
        "Volume": [r["v"] for r in rows],
    }, index=index)


class EquivalenceAgainstReferenceOracle(unittest.TestCase):
    """For every DataFrame shape below, the new itertuples()-based function
    must return exactly what the old iterrows() logic would have."""

    def _assert_equivalent(self, df, tkr="RELIANCE"):
        # Copy the df for each call -- both implementations may mutate
        # columns (MultiIndex flattening / missing-column fill-in), and we
        # need each to see a pristine input for a fair comparison.
        expected = _reference_iterrows_implementation(df.copy(deep=True), tkr)
        actual = _yf_daily_df_to_candle_rows(df.copy(deep=True), tkr)
        self.assertEqual(actual, expected)

    def test_normal_multi_row_dataframe(self):
        df = _make_df([
            {"date": "2026-01-02", "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 150000},
            {"date": "2026-01-03", "o": 103.5, "h": 108.0, "l": 102.0, "c": 107.0, "v": 220000},
            {"date": "2026-01-06", "o": 107.0, "h": 107.5, "l": 104.0, "c": 105.0, "v": 180000},
        ])
        self._assert_equivalent(df)

    def test_single_row_dataframe(self):
        df = _make_df([{"date": "2026-02-14", "o": 50.0, "h": 51.0, "l": 49.5, "c": 50.5, "v": 9000}])
        self._assert_equivalent(df)

    def test_empty_dataframe_returns_empty_list(self):
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        expected = _reference_iterrows_implementation(df.copy(), "INFY")
        actual = _yf_daily_df_to_candle_rows(df.copy(), "INFY")
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [])

    def test_none_dataframe_returns_empty_list(self):
        self.assertEqual(_yf_daily_df_to_candle_rows(None, "INFY"), [])

    def test_zero_close_row_is_excluded_by_both(self):
        df = _make_df([
            {"date": "2026-01-02", "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000},
            {"date": "2026-01-03", "o": 0.0, "h": 0.0, "l": 0.0, "c": 0.0, "v": 0},  # halted/no-trade day
        ])
        self._assert_equivalent(df)
        # And explicitly: only the valid row survives.
        result = _yf_daily_df_to_candle_rows(df.copy(), "TCS")
        self.assertEqual(len(result), 1)

    def test_negative_open_row_is_excluded_by_both(self):
        df = _make_df([{"date": "2026-01-02", "o": -1.0, "h": 5.0, "l": -2.0, "c": 3.0, "v": 500}])
        self._assert_equivalent(df)
        self.assertEqual(_yf_daily_df_to_candle_rows(df.copy(), "X"), [])

    def test_nan_close_row_excluded_identically_by_both(self):
        """A present-but-NaN Close: `NaN or 0` is NaN (NaN is truthy), so
        the OLD code's `c > 0` guard is False (NaN comparisons are always
        False) and the row is dropped. The new code must drop it the same
        way, for the same underlying reason -- not because of some
        different, coincidental code path."""
        df = _make_df([{"date": "2026-01-02", "o": 100.0, "h": 105.0, "l": 98.0, "c": float("nan"), "v": 1000}])
        self._assert_equivalent(df)
        self.assertEqual(_yf_daily_df_to_candle_rows(df.copy(), "X"), [])

    def test_nan_volume_raises_valueerror_identically_by_both(self):
        """A present-but-NaN Volume: `int(NaN or 0)` raises ValueError in
        the OLD code (NaN survives `or 0` since NaN is truthy, then
        int(nan) is a hard error) -- the caller (_fetch_daily) already
        wraps this in a try/except, so today one bad row's NaN volume marks
        the WHOLE ticker as failed for this cycle. That existing failure
        mode must be preserved exactly, not silently softened, by this
        performance-only change."""
        df = _make_df([{"date": "2026-01-02", "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": float("nan")}])
        with self.assertRaises(ValueError):
            _reference_iterrows_implementation(df.copy(), "X")
        with self.assertRaises(ValueError):
            _yf_daily_df_to_candle_rows(df.copy(), "X")

    def test_missing_column_falls_back_to_zero_identically(self):
        """Defensive path: yfinance always returns Volume in practice, but
        the old Series.get('Volume', 0) fallback must still be matched if
        it's ever missing -- itertuples() has no per-row default, so
        _yf_daily_df_to_candle_rows must pre-fill the column instead."""
        df = pd.DataFrame({
            "Open": [100.0], "High": [105.0], "Low": [98.0], "Close": [103.0],
        }, index=pd.to_datetime(["2026-01-02"]))
        expected = _reference_iterrows_implementation(df.copy(), "X")
        actual = _yf_daily_df_to_candle_rows(df.copy(), "X")
        self.assertEqual(actual, expected)
        # Volume defaults to 0, and the row still qualifies (O/H/L/C > 0).
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["v"], 0)

    def test_multiindex_columns_flattened_identically(self):
        """yfinance returns MultiIndex columns for a single-ticker
        yf.download() call in some versions -- both implementations must
        flatten to the first level the same way."""
        df = _make_df([{"date": "2026-01-02", "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000}])
        df.columns = pd.MultiIndex.from_product([df.columns, ["RELIANCE.NS"]])
        self._assert_equivalent(df)

    def test_timezone_aware_index_normalized_identically(self):
        df = _make_df(
            [{"date": "2026-01-02", "o": 100.0, "h": 105.0, "l": 98.0, "c": 103.0, "v": 1000}],
            tz="Asia/Kolkata",
        )
        expected = _reference_iterrows_implementation(df.copy(), "X")
        actual = _yf_daily_df_to_candle_rows(df.copy(), "X")
        self.assertEqual(actual, expected)
        # Both must strip tzinfo (candles are stored IST-naive).
        self.assertIsNone(actual[0]["ts"].tzinfo)

    def test_ohlc_normalization_max_min_matches_reference(self):
        """h/l are max(o,h,c)/min(o,l,c), not the raw yfinance High/Low --
        proves the itertuples() values feed the SAME downstream formula."""
        df = _make_df([{"date": "2026-01-02", "o": 110.0, "h": 105.0, "l": 120.0, "c": 108.0, "v": 1000}])
        result = _yf_daily_df_to_candle_rows(df.copy(), "X")
        self.assertEqual(result[0]["h"], max(110.0, 105.0, 108.0))
        self.assertEqual(result[0]["l"], min(110.0, 120.0, 108.0))
        self._assert_equivalent(df)


if __name__ == "__main__":
    unittest.main()
