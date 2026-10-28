"""Regression: a partial session must never rewrite a daily candle.

`_reconcile_daily_from_5m` gated only on `min_bars = 3`, so CUPID 2026-10-28 (32 of 75
5m bars, 09:15-11:50) overwrote the exchange daily with a truncated one:
close 315.75 instead of 314.55, low 309.35 instead of 309.00, volume 6.4M instead of
10.7M. A full NSE session is 75 bars, so the gate must be ~complete.
"""
import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(
    encoding="utf-8", errors="replace")


def test_completeness_threshold():
    m = re.search(r"def _reconcile_daily_from_5m\(days_back: int = \d+, min_bars: int = (\d+)\)",
                  SRC)
    assert m, "signature not found"
    assert int(m.group(1)) >= 70, "min_bars must mean a complete session (75 bars), got %s" % m.group(1)


def test_threshold_is_documented_as_completeness():
    i = SRC.index("def _reconcile_daily_from_5m(")
    body = SRC[i:i + 900]
    assert "COMPLETENESS gate" in body
    assert "75 bars" in body


def test_no_caller_lowers_the_threshold():
    # nothing may pass a smaller min_bars and silently re-open the bug
    for m in re.finditer(r"_reconcile_daily_from_5m\(([^)]*)\)", SRC):
        args = m.group(1)
        vals = [v for v in args.split(",") if "min_bars" in v]
        for v in vals:
            n = re.search(r"(\d+)", v)
            assert n and int(n.group(1)) >= 70, "caller passes a low min_bars: " + args