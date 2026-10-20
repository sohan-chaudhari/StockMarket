"""Regression: the intraday page query must use equality for a single ticker."""
import pathlib

MAIN = pathlib.Path(__file__).resolve().parents[1] / "main.py"
SRC = MAIN.read_text(encoding="utf-8", errors="replace")


def _body():
    i = SRC.index("def get_intraday_paginated")
    return SRC[i:i + 4000]


def test_single_ticker_equality_branch():
    b = _body()
    assert "if len(_tk) == 1:" in b, "single-ticker equality branch missing"
    assert "q_base.filter(model.ticker == _tk[0])" in b
    assert "model.ticker.in_(_tk)" in b, "multi-spelling fallback must remain"


def test_unconditional_in_list_is_gone():
    b = _body()
    assert "model.ticker.in_(db_tickers), model.timeframe == interval" not in b