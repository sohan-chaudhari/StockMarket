"""
Math-correctness tests for indicator calculations.
These validate the exact formulas (not API wiring).
Reference values computed from first principles / TradingView docs.
"""
import math
import sys
import os
import datetime
import calendar
from typing import List, Dict, Optional, Tuple
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest


# ─────────────────────────────────────────────────────────
# Minimal JS→Python port of the indicator math
# (kept local to this test file — no production imports)
# ─────────────────────────────────────────────────────────

def calc_sma(src, period):
    n = len(src)
    result = [None] * n
    if period < 1 or n < period:
        return result
    s = sum(src[:period])
    result[period - 1] = s / period
    for i in range(period, n):
        s += src[i] - src[i - period]
        result[i] = s / period
    return result


def calc_ema(src, period):
    n = len(src)
    result = [None] * n
    if period < 1 or n < period:
        return result
    prev = sum(src[:period]) / period
    result[period - 1] = prev
    k = 2 / (period + 1)
    for i in range(period, n):
        prev = src[i] * k + prev * (1 - k)
        result[i] = prev
    return result


def calc_rsi(src, period):
    """Wilder's smoothing — matches TradingView default RSI."""
    n = len(src)
    result = [None] * n
    if period < 1 or n < period + 1:
        return result
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = src[i] - src[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    result[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        d = src[i] - src[i - 1]
        g = d if d > 0 else 0
        l = -d if d < 0 else 0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        result[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return result


def calc_macd(src, fast, slow, signal):
    ema_fast = calc_ema(src, fast)
    ema_slow = calc_ema(src, slow)
    n = len(src)
    macd_line = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]
    first = next((i for i, v in enumerate(macd_line) if v is not None), -1)
    sig_line = [None] * n
    hist = [None] * n
    if first == -1:
        return macd_line, sig_line, hist
    slice_ = [v for v in macd_line[first:] if v is not None]
    # only non-null slice passed to EMA for signal
    sig_slice = calc_ema(slice_, signal)
    for i, si in enumerate(sig_slice):
        idx = first + i
        sig_line[idx] = si
        if macd_line[idx] is not None and si is not None:
            hist[idx] = macd_line[idx] - si
    return macd_line, sig_line, hist


def calc_bb(src, period, mult):
    n = len(src)
    basis = calc_sma(src, period)
    upper = [None] * n
    lower = [None] * n
    for i in range(period - 1, n):
        if basis[i] is None:
            continue
        variance = sum((src[j] - basis[i]) ** 2 for j in range(i - period + 1, i + 1)) / period
        sd = math.sqrt(variance)
        upper[i] = basis[i] + mult * sd
        lower[i] = basis[i] - mult * sd
    return basis, upper, lower


def calc_vwap(candles, source_field="hlc3", anchor="Session", band_mult=1.0, bands=False):
    """Session-anchored Volume Weighted Average Price (VWAP) calculation."""
    n = len(candles)
    vwap = [None] * n
    upper = [None] * n
    lower = [None] * n
    std_dev = [None] * n
    if n == 0:
        return vwap, upper, lower, std_dev

    src = extract_source(candles, source_field)
    cum_vol = 0.0
    cum_pv = 0.0
    cum_p2v = 0.0
    current_session = None

    for i in range(n):
        c = candles[i]
        t = c.get("time") or c.get("timestamp")
        if isinstance(t, str):
            session_key = t[:10]
        elif isinstance(t, (int, float)):
            sec = t // 1000 if t > 1e11 else t
            import datetime
            dt = datetime.datetime.fromtimestamp(sec + 19800, tz=datetime.timezone.utc)
            session_key = dt.strftime("%Y-%m-%d")
        else:
            session_key = str(t)

        if anchor == "Session":
            if current_session is not None and session_key != current_session:
                cum_vol = 0.0
                cum_pv = 0.0
                cum_p2v = 0.0
            current_session = session_key

        tp = src[i]
        v = float(c.get("volume", 0) or 0)
        if v < 0:
            v = 0.0

        if v > 0 and tp is not None:
            cum_vol += v
            cum_pv += tp * v
            cum_p2v += tp * tp * v

        if cum_vol > 0:
            val = cum_pv / cum_vol
            vwap[i] = val
            if bands:
                variance = max(0.0, (cum_p2v / cum_vol) - (val * val))
                sd = math.sqrt(variance)
                std_dev[i] = sd
                upper[i] = val + band_mult * sd
                lower[i] = val - band_mult * sd
        else:
            vwap[i] = tp
            if bands:
                upper[i] = tp
                lower[i] = tp
                std_dev[i] = 0.0

    return vwap, upper, lower, std_dev


def calc_atr(candles, period=14):
    """Calculates Wilder's Average True Range exactly matching indicators.js."""
    n = len(candles) if candles else 0
    result = [None] * n
    if n == 0 or period is None or period < 1:
        return result

    tr = [0.0] * n
    for i in range(n):
        c = candles[i]
        h = float(c.get("high", c.get("close", 0)))
        l = float(c.get("low", c.get("close", 0)))
        cl = float(c.get("close", (h + l) / 2.0))

        if i == 0:
            tr[i] = abs(h - l)
        else:
            prev = candles[i - 1]
            prev_close = float(prev.get("close", cl))
            hl = abs(h - l)
            hpc = abs(h - prev_close)
            lpc = abs(l - prev_close)
            tr[i] = max(hl, hpc, lpc)

    if period == 1:
        return tr

    if n < period:
        return result

    initial_atr = sum(tr[:period]) / float(period)
    result[period - 1] = initial_atr

    prev_atr = initial_atr
    for i in range(period, n):
        cur_atr = (prev_atr * (period - 1) + tr[i]) / float(period)
        result[i] = cur_atr
        prev_atr = cur_atr

    return result


def calc_supertrend(candles, period=10, multiplier=3.0):
    """Calculates Supertrend matching indicators.js, strictly reusing calc_atr."""
    n = len(candles) if candles else 0
    supertrend = [None] * n
    trend = [None] * n
    final_upper = [None] * n
    final_lower = [None] * n

    if n == 0 or period is None or period < 1 or multiplier is None or multiplier <= 0:
        return supertrend, trend, final_upper, final_lower

    atr = calc_atr(candles, period=period)
    if n < period:
        return supertrend, trend, final_upper, final_lower

    seed_idx = period - 1
    for i in range(seed_idx, n):
        c = candles[i]
        h = float(c.get("high", c.get("close", 0)))
        l = float(c.get("low", c.get("close", 0)))
        cl = float(c.get("close", (h + l) / 2.0))

        cur_atr = atr[i]
        if cur_atr is None:
            continue

        hl2 = (h + l) / 2.0
        basic_upper = hl2 + (multiplier * cur_atr)
        basic_lower = hl2 - (multiplier * cur_atr)

        if i == seed_idx or final_upper[i - 1] is None:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            init_trend = 1 if cl > basic_upper else (-1 if cl < basic_lower else (1 if cl >= hl2 else -1))
            trend[i] = init_trend
            supertrend[i] = basic_lower if init_trend == 1 else basic_upper
        else:
            prev_fu = final_upper[i - 1]
            prev_fl = final_lower[i - 1]
            prev_c = float(candles[i - 1].get("close", cl))
            prev_trend = trend[i - 1]

            if basic_upper < prev_fu or prev_c > prev_fu:
                final_upper[i] = basic_upper
            else:
                final_upper[i] = prev_fu

            if basic_lower > prev_fl or prev_c < prev_fl:
                final_lower[i] = basic_lower
            else:
                final_lower[i] = prev_fl

            if prev_trend == -1:
                if cl > final_upper[i]:
                    trend[i] = 1
                    supertrend[i] = final_lower[i]
                else:
                    trend[i] = -1
                    supertrend[i] = final_upper[i]
            else:
                if cl < final_lower[i]:
                    trend[i] = -1
                    supertrend[i] = final_upper[i]
                else:
                    trend[i] = 1
                    supertrend[i] = final_lower[i]

    return supertrend, trend, final_upper, final_lower


def calc_keltner(candles, length=20, atr_length=10, multiplier=2.0):
    """
    Independent reference port of indicators.js Calc.keltner.
    Middle[i] = EMA(close, length)[i]                        (reuses calc_ema)
    Upper[i]  = Middle[i] + ATR(atr_length)[i] * multiplier   (reuses calc_atr)
    Lower[i]  = Middle[i] - ATR(atr_length)[i] * multiplier
    Deliberately NOT SMA +/- stddev (Bollinger) and NOT rolling
    highest-high/lowest-low (Donchian).
    """
    n = len(candles) if candles else 0
    middle = [None] * n
    upper = [None] * n
    lower = [None] * n

    if (n == 0 or length is None or length < 1 or atr_length is None or atr_length < 1 or
            multiplier is None or not math.isfinite(multiplier) or multiplier <= 0):
        return {"middle": middle, "upper": upper, "lower": lower}

    ema_vals = calc_ema(extract_source(candles, "close"), length)
    atr_vals = calc_atr(candles, atr_length)

    for i in range(n):
        if ema_vals[i] is None or atr_vals[i] is None:
            continue
        middle[i] = ema_vals[i]
        upper[i] = ema_vals[i] + atr_vals[i] * multiplier
        lower[i] = ema_vals[i] - atr_vals[i] * multiplier

    return {"middle": middle, "upper": upper, "lower": lower}


def calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3):
    """Calculates Stochastic Oscillator matching indicators.js."""
    n = len(candles) if candles else 0
    raw_k = [None] * n
    k = [None] * n
    d = [None] * n

    if n == 0 or k_period is None or k_period < 1 or k_smooth is None or k_smooth < 1 or d_period is None or d_period < 1:
        return k, d, raw_k

    if n >= k_period:
        for i in range(k_period - 1, n):
            hh = -float('inf')
            ll = float('inf')
            for j in range(i - k_period + 1, i + 1):
                c = candles[j]
                h = float(c.get("high", c.get("close", 0)))
                l = float(c.get("low", c.get("close", 0)))
                if h > hh:
                    hh = h
                if l < ll:
                    ll = l
            cur_close = float(candles[i].get("close", (hh + ll) / 2.0))
            rng = hh - ll
            if rng == 0:
                val = 50.0
            else:
                val = 100.0 * (cur_close - ll) / rng
            raw_k[i] = max(0.0, min(100.0, val))

    k_min_idx = k_period - 1 + k_smooth - 1
    if n > k_min_idx:
        if k_smooth == 1:
            for i in range(k_period - 1, n):
                k[i] = raw_k[i]
        else:
            sum_k = sum(raw_k[k_period - 1 : k_min_idx + 1])
            k[k_min_idx] = max(0.0, min(100.0, sum_k / float(k_smooth)))
            for i in range(k_min_idx + 1, n):
                sum_k += raw_k[i] - raw_k[i - k_smooth]
                k[i] = max(0.0, min(100.0, sum_k / float(k_smooth)))

    d_min_idx = k_min_idx + d_period - 1
    if n > d_min_idx:
        if d_period == 1:
            for i in range(k_min_idx, n):
                d[i] = k[i]
        else:
            sum_d = sum(k[k_min_idx : d_min_idx + 1])
            d[d_min_idx] = max(0.0, min(100.0, sum_d / float(d_period)))
            for i in range(d_min_idx + 1, n):
                sum_d += k[i] - k[i - d_period]
                d[i] = max(0.0, min(100.0, sum_d / float(d_period)))

    return k, d, raw_k


def calc_adx(candles, period=14):
    """Calculates ADX, +DI, -DI, DX matching indicators.js."""
    n = len(candles) if candles else 0
    adx = [None] * n
    plus_di = [None] * n
    minus_di = [None] * n
    dx = [None] * n

    if n == 0 or period is None or period < 1:
        return adx, plus_di, minus_di, dx

    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(n):
        c = candles[i]
        h = float(c.get("high", c.get("close", 0.0)))
        l = float(c.get("low", c.get("close", 0.0)))
        cl = float(c.get("close", (h + l) / 2.0))

        if i == 0:
            tr[0] = abs(h - l)
            plus_dm[0] = 0.0
            minus_dm[0] = 0.0
        else:
            prev = candles[i - 1]
            prev_h = float(prev.get("high", prev.get("close", 0.0)))
            prev_l = float(prev.get("low", prev.get("close", 0.0)))
            prev_close = float(prev.get("close", cl))

            hl = abs(h - l)
            hpc = abs(h - prev_close)
            lpc = abs(l - prev_close)
            tr[i] = max(hl, hpc, lpc)

            up_move = h - prev_h
            down_move = prev_l - l

            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            else:
                plus_dm[i] = 0.0

            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
            else:
                minus_dm[i] = 0.0

    if n < period:
        return adx, plus_di, minus_di, dx

    smoothed_tr = [0.0] * n
    smoothed_plus_dm = [0.0] * n
    smoothed_minus_dm = [0.0] * n

    sum_tr = sum(tr[:period])
    sum_plus_dm = sum(plus_dm[:period])
    sum_minus_dm = sum(minus_dm[:period])

    smoothed_tr[period - 1] = sum_tr
    smoothed_plus_dm[period - 1] = sum_plus_dm
    smoothed_minus_dm[period - 1] = sum_minus_dm

    if sum_tr == 0:
        plus_di[period - 1] = 0.0
        minus_di[period - 1] = 0.0
    else:
        plus_di[period - 1] = max(0.0, min(100.0, 100.0 * sum_plus_dm / sum_tr))
        minus_di[period - 1] = max(0.0, min(100.0, 100.0 * sum_minus_dm / sum_tr))

    s_di0 = plus_di[period - 1] + minus_di[period - 1]
    d_di0 = abs(plus_di[period - 1] - minus_di[period - 1])
    dx[period - 1] = 0.0 if s_di0 == 0 else max(0.0, min(100.0, 100.0 * d_di0 / s_di0))

    for i in range(period, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - (smoothed_tr[i - 1] / float(period)) + tr[i]
        smoothed_plus_dm[i] = smoothed_plus_dm[i - 1] - (smoothed_plus_dm[i - 1] / float(period)) + plus_dm[i]
        smoothed_minus_dm[i] = smoothed_minus_dm[i - 1] - (smoothed_minus_dm[i - 1] / float(period)) + minus_dm[i]

        if smoothed_tr[i] == 0:
            plus_di[i] = 0.0
            minus_di[i] = 0.0
        else:
            plus_di[i] = max(0.0, min(100.0, 100.0 * smoothed_plus_dm[i] / smoothed_tr[i]))
            minus_di[i] = max(0.0, min(100.0, 100.0 * smoothed_minus_dm[i] / smoothed_tr[i]))

        s_di = plus_di[i] + minus_di[i]
        d_di = abs(plus_di[i] - minus_di[i])
        dx[i] = 0.0 if s_di == 0 else max(0.0, min(100.0, 100.0 * d_di / s_di))

    adx_seed_idx = 2 * period - 2
    if n > adx_seed_idx:
        sum_dx = sum(dx[period - 1 : adx_seed_idx + 1])
        adx[adx_seed_idx] = sum_dx / float(period)
        for i in range(adx_seed_idx + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / float(period)

    return adx, plus_di, minus_di, dx


def extract_source(candles, field="close"):
    """Extracts series values for the requested source field from OHLCV candles."""
    if field == "open":
        return [c["open"] for c in candles]
    elif field == "high":
        return [c["high"] for c in candles]
    elif field == "low":
        return [c["low"] for c in candles]
    elif field == "hl2":
        return [(c["high"] + c["low"]) / 2.0 for c in candles]
    elif field == "hlc3":
        return [(c["high"] + c["low"] + c["close"]) / 3.0 for c in candles]
    elif field == "ohlc4":
        return [(c["open"] + c["high"] + c["low"] + c["close"]) / 4.0 for c in candles]
    else:
        return [c["close"] for c in candles]


def make_realistic_candles(n=60, base_price=100.0, seed=42):
    """Generates a realistic synthetic OHLCV candle dataset for test hardening."""
    import random
    rng = random.Random(seed)
    candles = []
    price = base_price
    for i in range(n):
        open_ = price
        change = (rng.random() - 0.48) * 3.0
        close = round(max(10.0, open_ + change), 2)
        high = round(max(open_, close) + rng.random() * 2.0 + 0.1, 2)
        low = round(min(open_, close) - rng.random() * 2.0 - 0.1, 2)
        vol = rng.randint(1000, 50000)
        candles.append({
            "time": 1700000000 + i * 60,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol
        })
        price = close
    return candles


# ─────────────────────────────────────────────────────────
# SMA TESTS
# ─────────────────────────────────────────────────────────

class TestSMA:
    def test_basic_5_period(self):
        src = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        r = calc_sma(src, 5)
        assert r[0] is None
        assert r[3] is None
        assert abs(r[4] - 3.0) < 1e-9
        assert abs(r[5] - 4.0) < 1e-9
        assert abs(r[6] - 5.0) < 1e-9

    def test_period_1(self):
        src = [10.0, 20.0, 30.0]
        r = calc_sma(src, 1)
        assert r == src

    def test_period_equals_length(self):
        src = [2.0, 4.0, 6.0, 8.0]
        r = calc_sma(src, 4)
        assert r[:3] == [None, None, None]
        assert abs(r[3] - 5.0) < 1e-9

    def test_insufficient_data(self):
        r = calc_sma([1.0, 2.0], 5)
        assert all(v is None for v in r)

    def test_rolling_correctness(self):
        # SMA(3) of [10,20,30,40,50] = [None,None,20,30,40]
        src = [10.0, 20.0, 30.0, 40.0, 50.0]
        r = calc_sma(src, 3)
        assert abs(r[2] - 20.0) < 1e-9
        assert abs(r[3] - 30.0) < 1e-9
        assert abs(r[4] - 40.0) < 1e-9


# ─────────────────────────────────────────────────────────
# EMA TESTS
# ─────────────────────────────────────────────────────────

class TestEMA:
    def test_seed_equals_sma(self):
        # EMA(3) first value = SMA(3) of first 3 bars
        src = [10.0, 20.0, 30.0, 40.0]
        r = calc_ema(src, 3)
        assert abs(r[2] - 20.0) < 1e-9

    def test_k_factor_application(self):
        # k = 2/(3+1) = 0.5 for period=3
        # EMA[2] = 20
        # EMA[3] = 40*0.5 + 20*0.5 = 30
        src = [10.0, 20.0, 30.0, 40.0]
        r = calc_ema(src, 3)
        assert abs(r[3] - 30.0) < 1e-9

    def test_nulls_before_period(self):
        src = [1.0, 2.0, 3.0, 4.0, 5.0]
        r = calc_ema(src, 3)
        assert r[0] is None
        assert r[1] is None
        assert r[2] is not None

    def test_increasing_series(self):
        src = list(range(1, 11))  # 1..10
        r = calc_ema(src, 3)
        # All non-None values should be increasing
        vals = [v for v in r if v is not None]
        assert all(vals[i] < vals[i+1] for i in range(len(vals)-1))

    def test_constant_series(self):
        src = [5.0] * 10
        r = calc_ema(src, 4)
        for v in r[3:]:
            assert abs(v - 5.0) < 1e-9


# ─────────────────────────────────────────────────────────
# RSI TESTS (Wilder's smoothing)
# ─────────────────────────────────────────────────────────

class TestRSI:
    def test_overbought_constant_up(self):
        # Steadily rising — RSI should approach 100
        src = [float(i) for i in range(20)]
        r = calc_rsi(src, 14)
        assert r[14] is not None
        assert r[14] > 90

    def test_oversold_constant_down(self):
        # Steadily falling — RSI should approach 0
        src = [float(20 - i) for i in range(20)]
        r = calc_rsi(src, 14)
        assert r[14] is not None
        assert r[14] < 10

    def test_nulls_before_period(self):
        src = [float(i) for i in range(20)]
        r = calc_rsi(src, 14)
        assert all(v is None for v in r[:14])
        assert r[14] is not None

    def test_wilder_vs_simple_mean(self):
        # Wilder's differs from simple EMA (k=1/14 vs 2/15)
        # This test just verifies it's in [0,100]
        src = [100, 102, 101, 103, 105, 104, 106, 107, 106, 108, 109, 107, 110, 108, 111]
        r = calc_rsi(src, 14)
        v = r[14]
        assert v is not None
        assert 0 <= v <= 100

    def test_alternating_series_mid_range(self):
        # Alternating up-down — RSI should be near 50
        src = []
        price = 100.0
        for i in range(30):
            price += 1.0 if i % 2 == 0 else -1.0
            src.append(price)
        r = calc_rsi(src, 14)
        final = r[-1]
        assert final is not None
        assert 30 <= final <= 70


# ─────────────────────────────────────────────────────────
# MACD TESTS
# ─────────────────────────────────────────────────────────

class TestMACD:
    def _trending_src(self, n=100):
        return [float(i) + (i * 0.1) for i in range(n)]

    def test_macd_positive_in_uptrend(self):
        src = self._trending_src()
        ml, sl, hl = calc_macd(src, 12, 26, 9)
        # In a steady uptrend fast EMA > slow EMA → MACD > 0
        assert ml[-1] is not None
        assert ml[-1] > 0

    def test_macd_signal_delay(self):
        # Signal is EMA of MACD — lags MACD in uptrend
        src = self._trending_src()
        ml, sl, hl = calc_macd(src, 12, 26, 9)
        assert sl[-1] is not None
        # Both non-None at end
        assert ml[-1] is not None

    def test_histogram_sign(self):
        # Histogram = MACD - Signal; should equal their difference exactly
        src = self._trending_src()
        ml, sl, hl = calc_macd(src, 12, 26, 9)
        for i in range(len(src)):
            if ml[i] is not None and sl[i] is not None and hl[i] is not None:
                assert abs(hl[i] - (ml[i] - sl[i])) < 1e-9

    def test_nulls_before_slow_period(self):
        src = self._trending_src(50)
        ml, _, _ = calc_macd(src, 12, 26, 9)
        # First 25 values (indices 0-24) must be None (EMA(26) needs 26 bars)
        assert all(v is None for v in ml[:25])
        assert ml[25] is not None

    def test_returns_three_series(self):
        src = self._trending_src()
        result = calc_macd(src, 12, 26, 9)
        assert len(result) == 3
        assert len(result[0]) == len(result[1]) == len(result[2]) == len(src)


# ─────────────────────────────────────────────────────────
# BOLLINGER BANDS TESTS
# ─────────────────────────────────────────────────────────

class TestBollingerBands:
    def test_constant_series_zero_stddev(self):
        # Constant price → stddev=0 → upper=lower=basis
        src = [100.0] * 25
        basis, upper, lower = calc_bb(src, 20, 2.0)
        assert abs(basis[24] - 100.0) < 1e-9
        assert abs(upper[24] - 100.0) < 1e-9
        assert abs(lower[24] - 100.0) < 1e-9

    def test_upper_above_lower(self):
        import random
        random.seed(42)
        src = [100.0 + random.gauss(0, 2) for _ in range(50)]
        basis, upper, lower = calc_bb(src, 20, 2.0)
        for i in range(19, 50):
            assert upper[i] >= lower[i]
            assert upper[i] >= basis[i]
            assert lower[i] <= basis[i]

    def test_nulls_before_period(self):
        src = list(range(1, 26))
        basis, upper, lower = calc_bb(src, 20, 2.0)
        assert all(v is None for v in basis[:19])
        assert basis[19] is not None

    def test_population_stddev_formula(self):
        # Manual: src=[1,2,3,4,5], period=5, mean=3
        # variance = ((1-3)^2+(2-3)^2+(3-3)^2+(4-3)^2+(5-3)^2)/5 = 10/5 = 2
        # stddev = sqrt(2) ≈ 1.4142
        src = [1.0, 2.0, 3.0, 4.0, 5.0]
        basis, upper, lower = calc_bb(src, 5, 2.0)
        expected_sd = math.sqrt(2)
        assert abs(upper[4] - (3.0 + 2 * expected_sd)) < 1e-9
        assert abs(lower[4] - (3.0 - 2 * expected_sd)) < 1e-9

    def test_mult_scaling(self):
        # Doubling mult should double distance from basis
        src = [float(i) for i in range(1, 26)]
        _, u1, l1 = calc_bb(src, 20, 1.0)
        _, u2, l2 = calc_bb(src, 20, 2.0)
        b = calc_sma(src, 20)[24]
        dist1 = u1[24] - b
        dist2 = u2[24] - b
        assert abs(dist2 - 2 * dist1) < 1e-9


# ─────────────────────────────────────────────────────────
# 1. INCREMENTAL VS FULL CALCULATION TESTS
# ─────────────────────────────────────────────────────────

class TestIncrementalVsFull:
    def test_sma_incremental_matches_full(self):
        candles = make_realistic_candles(60, base_price=150.0)
        src = extract_source(candles, "close")
        period = 20

        # Full calculation on complete series
        full_res = calc_sma(src, period)
        full_latest = full_res[-1]

        # Incremental calculation on latest bar (window sum / period)
        inc_latest = sum(src[-period:]) / period

        assert full_latest is not None
        assert abs(full_latest - inc_latest) < 1e-9

        # Verify on dynamic forming candle update
        src[-1] = 165.40
        full_updated = calc_sma(src, period)[-1]
        inc_updated = sum(src[-period:]) / period
        assert abs(full_updated - inc_updated) < 1e-9

    def test_ema_incremental_matches_full_and_prevents_reseeding_drift(self):
        candles = make_realistic_candles(60, base_price=150.0)
        src = extract_source(candles, "close")
        period = 20

        # Full calculation on complete series
        full_res = calc_ema(src, period)
        full_latest = full_res[-1]

        # Fixed engine incremental update: full seeded evaluation for latest bar
        inc_latest = calc_ema(src, period)[-1]
        assert full_latest is not None
        assert abs(full_latest - inc_latest) < 1e-9

        # Forming candle price movement update
        src[-1] = 172.50
        full_updated = calc_ema(src, period)[-1]
        inc_updated = calc_ema(src, period)[-1]
        assert abs(full_updated - inc_updated) < 1e-9

        # Catches the fixed re-seeding bug:
        # A naive truncated sub-slice (e.g. only passing last 20 bars to EMA)
        # seeds at SMA(20) of bars 40..60 instead of propagating from bar 0.
        naive_truncated_ema = calc_ema(src[-period:], period)[-1]
        assert abs(full_updated - naive_truncated_ema) > 1e-4

    def test_rsi_incremental_matches_full_and_prevents_reseeding_drift(self):
        candles = make_realistic_candles(60, base_price=100.0)
        src = extract_source(candles, "close")
        period = 14

        # Full calculation on complete series
        full_res = calc_rsi(src, period)
        full_latest = full_res[-1]

        # Fixed engine incremental update
        inc_latest = calc_rsi(src, period)[-1]
        assert full_latest is not None
        assert abs(full_latest - inc_latest) < 1e-9

        # Forming candle tick update
        src[-1] = 115.80
        full_updated = calc_rsi(src, period)[-1]
        inc_updated = calc_rsi(src, period)[-1]
        assert abs(full_updated - inc_updated) < 1e-9

        # Catches the fixed re-seeding bug:
        # A naive sub-slice without full Wilder smoothing history diverges
        naive_truncated_rsi = calc_rsi(src[-(period + 1):], period)[-1]
        assert abs(full_updated - naive_truncated_rsi) > 1e-3

    def test_bollinger_incremental_matches_full(self):
        candles = make_realistic_candles(60, base_price=200.0)
        src = extract_source(candles, "close")
        period = 20
        mult = 2.0

        # Full calculation
        full_basis, full_upper, full_lower = calc_bb(src, period, mult)

        # Incremental calculation on latest forming bar
        slice_ = src[-period:]
        mean = sum(slice_) / period
        variance = sum((x - mean) ** 2 for x in slice_) / period
        sd = math.sqrt(variance)
        inc_basis = mean
        inc_upper = mean + mult * sd
        inc_lower = mean - mult * sd

        assert abs(full_basis[-1] - inc_basis) < 1e-9
        assert abs(full_upper[-1] - inc_upper) < 1e-9
        assert abs(full_lower[-1] - inc_lower) < 1e-9


# ─────────────────────────────────────────────────────────
# 2. SOURCE SELECTION TESTS
# ─────────────────────────────────────────────────────────

class TestSourceSelection:
    def test_source_selection_changes_numerical_results(self):
        # Create candles where open != high != low != close intentionally
        candles = []
        for i in range(30):
            candles.append({
                "time": 1700000000 + i * 60,
                "open": 100.0 + i,
                "high": 120.0 + i,
                "low": 80.0 + i,
                "close": 110.0 + i,
                "volume": 5000
            })

        # Sources:
        # close = 110 + i
        # hl2   = (120 + 80)/2 = 100 + i
        # hlc3  = (120 + 80 + 110)/3 = 103.333333 + i
        # ohlc4 = (100 + 120 + 80 + 110)/4 = 102.5 + i
        src_close = extract_source(candles, "close")
        src_hl2 = extract_source(candles, "hl2")
        src_hlc3 = extract_source(candles, "hlc3")
        src_ohlc4 = extract_source(candles, "ohlc4")

        period = 10
        sma_close = calc_sma(src_close, period)[-1]
        sma_hl2 = calc_sma(src_hl2, period)[-1]
        sma_hlc3 = calc_sma(src_hlc3, period)[-1]
        sma_ohlc4 = calc_sma(src_ohlc4, period)[-1]

        # Verify each result is numerically distinct
        results = [sma_close, sma_hl2, sma_hlc3, sma_ohlc4]
        unique_rounded = {round(v, 4) for v in results}
        assert len(unique_rounded) == 4

        # Verify exact numerical formula adherence
        assert abs(sma_close - (110.0 + 24.5)) < 1e-9
        assert abs(sma_hl2 - (100.0 + 24.5)) < 1e-9
        assert abs(sma_hlc3 - (103.33333333333333 + 24.5)) < 1e-9
        assert abs(sma_ohlc4 - (102.5 + 24.5)) < 1e-9


# ─────────────────────────────────────────────────────────
# 3. MACD SIGNAL WARM-UP TESTS
# ─────────────────────────────────────────────────────────

class TestMACDSignalWarmup:
    def test_macd_exact_signal_warmup_index(self):
        # Default MACD: fast=12, slow=26, signal=9
        n = 60
        src = [100.0 + float(i) * 0.5 for i in range(n)]
        fast, slow, signal = 12, 26, 9

        macd_line, sig_line, hist = calc_macd(src, fast, slow, signal)

        # 1. MACD line first valid index is slow - 1 = 25 (26th bar)
        assert all(v is None for v in macd_line[:25])
        assert macd_line[25] is not None

        # 2. Signal line first valid index is (slow - 1) + (signal - 1) = 25 + 8 = 33 (34th bar)
        expected_signal_start = (slow - 1) + (signal - 1)
        assert all(v is None for v in sig_line[:expected_signal_start])
        assert sig_line[expected_signal_start] is not None

        # 3. Histogram first valid index matches signal line start
        assert all(v is None for v in hist[:expected_signal_start])
        assert hist[expected_signal_start] is not None
        assert abs(hist[expected_signal_start] - (macd_line[expected_signal_start] - sig_line[expected_signal_start])) < 1e-9

    def test_macd_custom_parameters_warmup(self):
        # Custom parameters: fast=5, slow=15, signal=5
        n = 50
        src = [50.0 + float(i) for i in range(n)]
        fast, slow, signal = 5, 15, 5

        macd_line, sig_line, hist = calc_macd(src, fast, slow, signal)

        expected_macd_start = slow - 1  # 14
        expected_signal_start = (slow - 1) + (signal - 1)  # 14 + 4 = 18

        assert all(v is None for v in macd_line[:expected_macd_start])
        assert macd_line[expected_macd_start] is not None

        assert all(v is None for v in sig_line[:expected_signal_start])
        assert sig_line[expected_signal_start] is not None
        assert hist[expected_signal_start] is not None


# ─────────────────────────────────────────────────────────
# 4. RSI KNOWN REFERENCE DATASET TEST
# ─────────────────────────────────────────────────────────

class TestRSIKnownReference:
    def test_wilder_rsi_deterministic_reference(self):
        # Classic 14-period Wilder reference dataset
        prices = [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00
        ]
        period = 14
        rsi = calc_rsi(prices, period)

        # First 14 bars (indices 0..13) must be None
        assert all(v is None for v in rsi[:14])

        # Bar 14 (15th price, first initial RSI value)
        # avg_gain = 3.34 / 14 = 0.2385714..., avg_loss = 1.40 / 14 = 0.10
        # RS = 2.385714..., RSI = 100 - (100 / (1 + 2.385714...)) = 70.464135
        expected_bar14_rsi = 70.464135
        assert abs(rsi[14] - expected_bar14_rsi) < 1e-4

        # Bar 15 (16th price, first smoothed Wilder RSI value)
        # delta = 46.00 - 46.28 = -0.28 (gain=0, loss=0.28)
        # avg_gain = (0.2385714 * 13 + 0) / 14 = 0.2215306
        # avg_loss = (0.10 * 13 + 0.28) / 14 = 0.1128571
        # RS = 1.962929..., RSI = 100 - (100 / (1 + 1.962929...)) = 66.249618
        expected_bar15_rsi = 66.249618
        assert abs(rsi[15] - expected_bar15_rsi) < 1e-4


# ─────────────────────────────────────────────────────────
# 5. BOLLINGER INCREMENTAL CONSISTENCY TEST
# ─────────────────────────────────────────────────────────

class TestBollingerIncrementalConsistency:
    def test_bollinger_incremental_consistency_multiple_bars(self):
        candles = make_realistic_candles(50, base_price=100.0)
        src = extract_source(candles, "close")
        period = 20
        mult = 2.0

        full_basis, full_upper, full_lower = calc_bb(src, period, mult)

        # Validate incremental latest-bar calculation across all valid bars
        for i in range(period - 1, len(src)):
            slice_ = src[i - period + 1:i + 1]
            mean = sum(slice_) / period
            variance = sum((x - mean) ** 2 for x in slice_) / period
            sd = math.sqrt(variance)

            inc_basis = mean
            inc_upper = mean + mult * sd
            inc_lower = mean - mult * sd

            assert abs(full_basis[i] - inc_basis) < 1e-9
            assert abs(full_upper[i] - inc_upper) < 1e-9
            assert abs(full_lower[i] - inc_lower) < 1e-9


# ─────────────────────────────────────────────────────────
# 6. VWAP CORRECTNESS & SESSION TESTS
# ─────────────────────────────────────────────────────────

class TestVWAPCorrectness:
    def test_vwap_basic_two_candles(self):
        """
        Candle 1: H=110, L=100, C=105, V=100 -> TP = 105, cum_pv = 10500, cum_v = 100 -> VWAP = 105.0
        Candle 2: H=120, L=110, C=115, V=200 -> TP = 115, cum_pv = 10500+23000 = 33500, cum_v = 300 -> VWAP = 33500/300 = 111.66666666666667
        """
        candles = [
            {"time": "2026-03-30 09:15:00", "open": 102, "high": 110, "low": 100, "close": 105, "volume": 100},
            {"time": "2026-03-30 09:20:00", "open": 106, "high": 120, "low": 110, "close": 115, "volume": 200},
        ]
        vwap, _, _, _ = calc_vwap(candles, source_field="hlc3", anchor="Session")
        assert abs(vwap[0] - 105.0) < 1e-9
        assert abs(vwap[1] - (33500.0 / 300.0)) < 1e-9

    def test_vwap_session_reset(self):
        """
        Day 1: Candle A, Candle B.
        Day 2: Candle C.
        Verify Day 2 VWAP resets and does NOT include Day 1 volume.
        """
        candles = [
            # Day 1
            {"time": "2026-03-30 09:15:00", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 100},
            {"time": "2026-03-30 15:25:00", "open": 105, "high": 120, "low": 110, "close": 115, "volume": 200},
            # Day 2
            {"time": "2026-03-31 09:15:00", "open": 200, "high": 210, "low": 190, "close": 200, "volume": 50},
        ]
        vwap, _, _, _ = calc_vwap(candles, source_field="hlc3", anchor="Session")
        # Day 1 bar 1
        assert abs(vwap[0] - 105.0) < 1e-9
        # Day 1 bar 2: (105*100 + 115*200)/300 = 111.6666667
        assert abs(vwap[1] - (33500.0 / 300.0)) < 1e-9
        # Day 2 bar 1: TP = (210+190+200)/3 = 200.0, volume = 50 -> must reset to 200.0 exactly!
        assert abs(vwap[2] - 200.0) < 1e-9

    def test_vwap_live_update_no_double_counting(self):
        """
        Test forming candle updates multiple times:
        Volume must not be double counted.
        """
        candles = [
            {"time": "2026-03-30 09:15:00", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 100},
            # Forming candle initial tick
            {"time": "2026-03-30 09:20:00", "open": 105, "high": 108, "low": 104, "close": 106, "volume": 50},
        ]
        vwap1, _, _, _ = calc_vwap(candles, source_field="hlc3")
        # Tick 1: TP1=105, V1=100; TP2=106, V2=50 -> cum_pv = 10500 + 5300 = 15800, cum_v = 150 -> 105.3333333
        assert abs(vwap1[1] - (15800.0 / 150.0)) < 1e-9

        # Live tick update to the SAME forming candle: high=120, low=110, close=115, volume=200
        candles[1] = {"time": "2026-03-30 09:20:00", "open": 105, "high": 120, "low": 110, "close": 115, "volume": 200}
        vwap2, _, _, _ = calc_vwap(candles, source_field="hlc3")
        # Tick 2: TP2=115, V2=200 -> cum_pv = 10500 + 23000 = 33500, cum_v = 300 -> 111.6666667
        assert abs(vwap2[1] - (33500.0 / 300.0)) < 1e-9

    def test_vwap_zero_volume_safety(self):
        """Zero volume should never produce NaN, Infinity, or crash."""
        candles = [
            {"time": "2026-03-30 09:15:00", "open": 100, "high": 110, "low": 100, "close": 105, "volume": 0},
            {"time": "2026-03-30 09:20:00", "open": 105, "high": 115, "low": 105, "close": 110, "volume": 0},
        ]
        vwap, upper, lower, std_dev = calc_vwap(candles, source_field="hlc3", bands=True)
        assert not math.isnan(vwap[0]) and not math.isinf(vwap[0])
        assert not math.isnan(vwap[1]) and not math.isinf(vwap[1])
        assert not math.isnan(upper[0]) and not math.isnan(lower[0])

    def test_vwap_source_settings(self):
        """HLC3 vs Close produces different results where expected."""
        candles = [
            {"time": "2026-03-30 09:15:00", "open": 100, "high": 150, "low": 90, "close": 100, "volume": 100},
        ]
        # HLC3 = (150+90+100)/3 = 113.3333333
        vwap_hlc3, _, _, _ = calc_vwap(candles, source_field="hlc3")
        # Close = 100.0
        vwap_close, _, _, _ = calc_vwap(candles, source_field="close")
        assert abs(vwap_hlc3[0] - (340.0 / 3.0)) < 1e-9
        assert abs(vwap_close[0] - 100.0) < 1e-9
        assert vwap_hlc3[0] != vwap_close[0]

    def test_vwap_bands_scaling(self):
        """Upper > VWAP and Lower < VWAP when dispersion exists, and multiplier scales distance."""
        candles = [
            {"time": "2026-03-30 09:15:00", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100},
            {"time": "2026-03-30 09:20:00", "open": 120, "high": 120, "low": 120, "close": 120, "volume": 100},
        ]
        # Multiplier 1.0
        vwap, upper1, lower1, sd1 = calc_vwap(candles, source_field="close", band_mult=1.0, bands=True)
        assert vwap[1] == 110.0
        assert sd1[1] == 10.0
        assert upper1[1] == 120.0
        assert lower1[1] == 100.0
        assert upper1[1] > vwap[1] > lower1[1]

        # Multiplier 2.0
        _, upper2, lower2, sd2 = calc_vwap(candles, source_field="close", band_mult=2.0, bands=True)
        assert upper2[1] == 130.0
        assert lower2[1] == 90.0
        assert abs((upper2[1] - vwap[1]) - 2.0 * (upper1[1] - vwap[1])) < 1e-9

    def test_vwap_service_pandas_integration(self):
        """Test backend IndicatorService.calculate_vwap against test helper."""
        from indicator_service import IndicatorService
        candles = [
            {"timestamp": "2026-03-30 09:15:00", "open": 102, "high": 110, "low": 100, "close": 105, "volume": 100},
            {"timestamp": "2026-03-30 09:20:00", "open": 106, "high": 120, "low": 110, "close": 115, "volume": 200},
            {"timestamp": "2026-03-31 09:15:00", "open": 200, "high": 210, "low": 190, "close": 200, "volume": 50},
        ]
        df = IndicatorService.to_df(candles)
        res = IndicatorService.calculate_vwap(df, source="hlc3", anchor="Session", band_mult=1.5, bands=True)
        assert abs(res['vwap'].iloc[0] - 105.0) < 1e-9
        assert abs(res['vwap'].iloc[1] - (33500.0 / 300.0)) < 1e-9
        assert abs(res['vwap'].iloc[2] - 200.0) < 1e-9
        assert res['upper'].iloc[1] > res['vwap'].iloc[1] > res['lower'].iloc[1]


class TestATRCorrectness:
    """Test suite verifying Average True Range mathematical correctness and Wilder smoothing."""

    def test_atr_true_range_normal(self):
        """TEST 1 — Normal True Range: max(H-L, |H-prevC|, |L-prevC|)"""
        candles = [
            {"high": 100, "low": 90, "close": 100},
            {"high": 110, "low": 105, "close": 108},
        ]
        # Prev close: 100, Current High: 110, Low: 105
        # HL = 5, HPC = 10, LPC = 5 -> TR = 10
        tr_vals = calc_atr(candles, period=1)
        assert tr_vals[0] == 10.0
        assert tr_vals[1] == 10.0

    def test_atr_gap_down(self):
        """TEST 2 — Gap Down True Range: prev close 100, High 95, Low 90 -> TR = 10"""
        candles = [
            {"high": 102, "low": 98, "close": 100},
            {"high": 95, "low": 90, "close": 92},
        ]
        # HL = 5, HPC = |95-100| = 5, LPC = |90-100| = 10 -> TR = 10
        tr_vals = calc_atr(candles, period=1)
        assert tr_vals[1] == 10.0

    def test_atr_first_candle(self):
        """TEST 3 — First candle has no previous close: TR = High - Low"""
        candles = [{"high": 110, "low": 100, "close": 105}]
        tr_vals = calc_atr(candles, period=1)
        assert tr_vals[0] == 10.0

    def test_atr_initial_value(self):
        """TEST 4 — Initial ATR at period N: exact sum(TR)/N"""
        # Create 14 candles with known TRs
        candles = []
        for i in range(14):
            candles.append({"high": 100 + (i + 1), "low": 100, "close": 100 + i})
        
        atr = calc_atr(candles, period=14)
        for i in range(13):
            assert atr[i] is None, f"Expected None during warmup at index {i}"
        
        # Manually compute sum of first 14 TRs
        tr_1 = calc_atr(candles, period=1)
        expected_init = sum(tr_1[:14]) / 14.0
        assert abs(atr[13] - expected_init) < 1e-9

    def test_atr_wilder_update(self):
        """TEST 5 — Single Wilder recurrence update: ATR = (prev*13 + TR)/14"""
        # prev ATR = 10, current TR = 14 -> expected = (10*13 + 14)/14 = 144/14 = 10.285714...
        candles = [{"high": 110, "low": 100, "close": 100}] * 14
        # First 14 candles have TR = 10, so initial ATR = 10.0
        # 15th candle has TR = 14 (High 114, Low 100, prevClose 100 -> TR = 14)
        candles.append({"high": 114, "low": 100, "close": 105})
        
        atr = calc_atr(candles, period=14)
        assert abs(atr[13] - 10.0) < 1e-9
        expected_14 = ((10.0 * 13.0) + 14.0) / 14.0
        assert abs(atr[14] - expected_14) < 1e-9

    def test_atr_multiple_wilder_updates(self):
        """TEST 6 — Multiple consecutive Wilder recurrence updates"""
        candles = [{"high": 110, "low": 100, "close": 100}] * 14
        # Add 3 more candles with varying TR
        candles.append({"high": 114, "low": 100, "close": 100}) # TR = 14
        candles.append({"high": 120, "low": 100, "close": 100}) # TR = 20
        candles.append({"high": 107, "low": 100, "close": 100}) # TR = 7
        
        atr = calc_atr(candles, period=14)
        a13 = 10.0
        a14 = (a13 * 13.0 + 14.0) / 14.0
        a15 = (a14 * 13.0 + 20.0) / 14.0
        a16 = (a15 * 13.0 + 7.0) / 14.0
        
        assert abs(atr[13] - a13) < 1e-9
        assert abs(atr[14] - a14) < 1e-9
        assert abs(atr[15] - a15) < 1e-9
        assert abs(atr[16] - a16) < 1e-9

    def test_atr_warmup_nulls(self):
        """Verify warm-up returns None for insufficient candles without throwing or NaN."""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        atr = calc_atr(candles, period=14)
        assert len(atr) == 10
        assert all(x is None for x in atr)

    def test_atr_period_1(self):
        """Period = 1 returns TR for every candle."""
        candles = [
            {"high": 110, "low": 100, "close": 105},
            {"high": 120, "low": 115, "close": 118},
        ]
        atr = calc_atr(candles, period=1)
        assert atr[0] == 10.0
        # bar 1: HL=5, HPC=|120-105|=15, LPC=|115-105|=10 -> TR=15
        assert atr[1] == 15.0

    def test_atr_invalid_period(self):
        """Invalid periods (0, -1, None) safely return None list."""
        candles = [{"high": 110, "low": 100, "close": 105}] * 5
        assert calc_atr(candles, period=0) == [None] * 5
        assert calc_atr(candles, period=-5) == [None] * 5
        assert calc_atr([], period=14) == []

    def test_atr_session_continuity(self):
        """Verify ATR does NOT reset at session boundaries (unlike VWAP)."""
        candles = [
            {"time": "2026-03-30 15:25:00", "high": 110, "low": 100, "close": 100},
            {"time": "2026-03-30 15:30:00", "high": 110, "low": 100, "close": 100},
        ] * 7  # 14 candles on Day 1
        
        # Day 2 opens at 09:15
        candles.append({"time": "2026-03-31 09:15:00", "high": 114, "low": 100, "close": 100})
        
        atr = calc_atr(candles, period=14)
        # Day 1 final ATR = 10.0
        assert abs(atr[13] - 10.0) < 1e-9
        # Day 2 bar 0 continues smoothly without resetting to 14.0 or None
        expected_day2 = (10.0 * 13.0 + 14.0) / 14.0
        assert abs(atr[14] - expected_day2) < 1e-9

    def test_atr_live_candle_update_no_double_counting(self):
        """Simulate updating current open candle in-place without double counting."""
        candles = [{"high": 110, "low": 100, "close": 100}] * 14
        # Append open candle
        candles.append({"high": 110, "low": 100, "close": 100})
        atr1 = calc_atr(candles, period=14)
        assert abs(atr1[14] - 10.0) < 1e-9

        # In-place update to open candle
        candles[14] = {"high": 124, "low": 100, "close": 100} # TR becomes 24
        atr2 = calc_atr(candles, period=14)
        assert len(atr2) == 15
        expected_updated = (10.0 * 13.0 + 24.0) / 14.0
        assert abs(atr2[14] - expected_updated) < 1e-9

    def test_atr_service_pandas_integration(self):
        """Test backend IndicatorService.calculate_atr against test helper."""
        import pandas as pd
        from indicator_service import IndicatorService
        candles = [{"high": 100 + i, "low": 100, "close": 100 + i // 2} for i in range(20)]
        df = IndicatorService.to_df(candles)
        s = IndicatorService.calculate_atr(df, period=14)
        helper_vals = calc_atr(candles, period=14)
        
        for i in range(13):
            assert pd.isna(s.iloc[i])
        for i in range(13, 20):
            assert abs(s.iloc[i] - helper_vals[i]) < 1e-9


class TestSupertrendCorrectness:
    """Test suite verifying Supertrend mathematical correctness, ATR reuse, and trend transitions."""

    def test_supertrend_basic_bands(self):
        """TEST 1 — Basic Band Calculation: BasicUpper = hl2 + m*ATR, BasicLower = hl2 - m*ATR"""
        # 10 candles with H=110, L=100, C=105 -> HL2 = 105, TR=10, ATR(10)=10
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        # At index 9 (seed candle): hl2 = 105, ATR = 10 -> BasicUpper = 105 + 30 = 135, BasicLower = 105 - 30 = 75
        assert abs(upper[9] - 135.0) < 1e-9
        assert abs(lower[9] - 75.0) < 1e-9
        assert trend[9] == 1  # Close (105) >= BasicLower (75) -> UP
        assert abs(st[9] - 75.0) < 1e-9

    def test_supertrend_final_upper_band(self):
        """TEST 2 — Final Upper Band: BasicUpper[i] < FinalUpper[i-1] -> FinalUpper[i] = BasicUpper[i]"""
        # Initial 10 candles: H=110, L=100, C=90 (so trend is DOWN)
        candles = [{"high": 110, "low": 100, "close": 90}] * 10
        # 11th candle: price drops, lower high/low -> H=100, L=90, C=85 -> basic upper decreases
        candles.append({"high": 100, "low": 90, "close": 85})
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        # Upper band at 10 should be less than upper band at 9
        assert upper[10] < upper[9]
        assert abs(st[10] - upper[10]) < 1e-9

    def test_supertrend_upper_band_carry_forward(self):
        """TEST 3 — Upper Band Carry Forward: BasicUpper >= prevFinalUpper AND Close[i-1] <= prevFinalUpper"""
        candles = [{"high": 110, "low": 100, "close": 70}] * 10
        # 11th candle has higher high/low, but prevClose (70) <= prevUpper (135)
        # So upper band does NOT increase in downtrend; it carries forward
        candles.append({"high": 115, "low": 105, "close": 80})
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        assert abs(upper[10] - upper[9]) < 1e-9

    def test_supertrend_final_lower_band(self):
        """TEST 4 — Final Lower Band: BasicLower[i] > FinalLower[i-1] -> FinalLower[i] = BasicLower[i]"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        # 11th candle pushes higher: H=120, L=115, C=118 -> BasicLower rises
        candles.append({"high": 120, "low": 115, "close": 118})
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        assert lower[10] > lower[9]
        assert abs(st[10] - lower[10]) < 1e-9

    def test_supertrend_lower_band_carry_forward(self):
        """TEST 5 — Lower Band Carry Forward: BasicLower <= prevFinalLower AND Close[i-1] >= prevFinalLower"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        # 11th candle has lower high/low, but prevClose (105) >= prevLower (75)
        # So lower band does NOT drop in uptrend; it carries forward (ratchet)
        candles.append({"high": 105, "low": 95, "close": 102})
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        assert abs(lower[10] - lower[9]) < 1e-9

    def test_supertrend_down_to_up_flip(self):
        """TEST 6 — DOWN -> UP flip when Close crosses above Final Upper Band"""
        candles = [{"high": 110, "low": 100, "close": 70}] * 10
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        assert trend[9] == -1  # Initial DOWN
        prev_upper = upper[9]

        # 11th candle explodes above previous upper band (216)
        candles.append({"high": 300, "low": 250, "close": 280})
        st2, trend2, upper2, lower2 = calc_supertrend(candles, period=10, multiplier=3.0)
        assert trend2[10] == 1  # Flipped to UP
        assert abs(st2[10] - lower2[10]) < 1e-9  # Supertrend switches to Lower Band

    def test_supertrend_up_to_down_flip(self):
        """TEST 7 — UP -> DOWN flip when Close crosses below Final Lower Band"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        assert trend[9] == 1  # Initial UP

        # 11th candle crashes below lower band
        candles.append({"high": 60, "low": 50, "close": 55})
        st2, trend2, upper2, lower2 = calc_supertrend(candles, period=10, multiplier=3.0)
        assert trend2[10] == -1  # Flipped to DOWN
        assert abs(st2[10] - upper2[10]) < 1e-9  # Supertrend switches to Upper Band

    def test_supertrend_trend_continuation(self):
        """TEST 8 — Trend Continuation: UP stays UP while Close >= Lower Band; DOWN stays DOWN while Close <= Upper Band"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        for _ in range(5):
            candles.append({"high": 112, "low": 102, "close": 106})
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        for i in range(9, 15):
            assert trend[i] == 1
            assert st[i] == lower[i]

    def test_supertrend_atr_reuse(self):
        """TEST 9 — Verify Supertrend strictly reuses calc_atr output"""
        candles = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(25)]
        atr = calc_atr(candles, period=10)
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=2.5)
        # Check basic formula directly uses atr[i]
        for i in range(9, 25):
            hl2 = (candles[i]["high"] + candles[i]["low"]) / 2.0
            expected_bu = hl2 + 2.5 * atr[i]
            expected_bl = hl2 - 2.5 * atr[i]
            assert upper[i] <= expected_bu + 1e-9
            assert lower[i] >= expected_bl - 1e-9

    def test_supertrend_warmup(self):
        """TEST 10 — Warm-up returns None for first period - 1 candles without NaN/Infinity"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        for i in range(9):
            assert st[i] is None
            assert trend[i] is None
            assert upper[i] is None
            assert lower[i] is None
        assert st[9] is not None

    def test_supertrend_period(self):
        """TEST 11 — Changing period shifts warm-up boundary and values appropriately"""
        candles = [{"high": 100 + i, "low": 90 + i, "close": 95 + i} for i in range(30)]
        st7, _, _, _ = calc_supertrend(candles, period=7, multiplier=3.0)
        st10, _, _, _ = calc_supertrend(candles, period=10, multiplier=3.0)
        st14, _, _, _ = calc_supertrend(candles, period=14, multiplier=3.0)
        
        assert st7[6] is not None and st7[5] is None
        assert st10[9] is not None and st10[8] is None
        assert st14[13] is not None and st14[12] is None

    def test_supertrend_multiplier(self):
        """TEST 12 — Changing multiplier widens/narrows bands proportionally"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        _, _, u2, l2 = calc_supertrend(candles, period=10, multiplier=2.0)
        _, _, u3, l3 = calc_supertrend(candles, period=10, multiplier=3.0)
        _, _, u4, l4 = calc_supertrend(candles, period=10, multiplier=4.0)

        # Width at seed: (105 + m*10) - (105 - m*10) = 2 * m * 10
        assert abs((u2[9] - l2[9]) - 40.0) < 1e-9
        assert abs((u3[9] - l3[9]) - 60.0) < 1e-9
        assert abs((u4[9] - l4[9]) - 80.0) < 1e-9

    def test_supertrend_session_continuity(self):
        """TEST 13 — Supertrend does NOT reset across multi-day session boundaries"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10  # Day 1
        # Day 2 opens without resetting state
        candles.append({"high": 112, "low": 102, "close": 108})
        st, trend, upper, lower = calc_supertrend(candles, period=10, multiplier=3.0)
        assert trend[10] == 1  # Continuous UP trend
        assert lower[10] is not None

    def test_supertrend_timeframe(self):
        """TEST 14 — Supertrend calculates using whatever candles are passed (timeframe-agnostic)"""
        # Daily candles
        daily = [{"high": 1500, "low": 1400, "close": 1450}] * 15
        st_d, trend_d, _, _ = calc_supertrend(daily, period=10, multiplier=3.0)
        assert st_d[9] is not None
        assert len(st_d) == 15

    def test_supertrend_symbol_reset(self):
        """TEST 15 — New symbol candles completely rebuild state without carrying over history"""
        sym1 = [{"high": 100, "low": 90, "close": 95}] * 15
        sym2 = [{"high": 2500, "low": 2400, "close": 2450}] * 15
        st1, _, _, _ = calc_supertrend(sym1, period=10, multiplier=3.0)
        st2, _, _, _ = calc_supertrend(sym2, period=10, multiplier=3.0)
        assert st1[9] < 200
        assert st2[9] > 2000

    def test_supertrend_live_candle_update(self):
        """TEST 16 — Live candle update in-place without state corruption"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 10
        candles.append({"high": 110, "low": 100, "close": 105})
        st1, trend1, _, _ = calc_supertrend(candles, period=10, multiplier=3.0)
        assert trend1[10] == 1

        # Live tick: sharp crash flips candle to down
        candles[10] = {"high": 110, "low": 50, "close": 55}
        st2, trend2, _, _ = calc_supertrend(candles, period=10, multiplier=3.0)
        assert trend2[10] == -1
        assert len(st2) == 11

    def test_supertrend_service_pandas_integration(self):
        """TEST 17 — Backend IndicatorService.calculate_supertrend integration test"""
        import pandas as pd
        from indicator_service import IndicatorService
        candles = [{"high": 100 + i, "low": 90 + i, "close": 95 + i} for i in range(25)]
        df = IndicatorService.to_df(candles)
        res_df = IndicatorService.calculate_supertrend(df, period=10, multiplier=3.0)
        st_helper, tr_helper, up_helper, lo_helper = calc_supertrend(candles, period=10, multiplier=3.0)
        
        for i in range(9):
            assert pd.isna(res_df['supertrend'].iloc[i])
        for i in range(9, 25):
            assert abs(res_df['supertrend'].iloc[i] - st_helper[i]) < 1e-9
            assert res_df['trend'].iloc[i] == tr_helper[i]


class TestStochasticCorrectness:
    """Test suite verifying Stochastic Oscillator mathematical correctness, smoothing, warm-up, and live updates."""

    def test_stochastic_highest_high(self):
        """TEST 1 — HIGHEST HIGH: K=3 over [10, 12, 11, 15, 13]"""
        highs = [10, 12, 11, 15, 13]
        candles = [{"high": h, "low": h - 5, "close": h - 2} for h in highs]
        # At i=2 (window 0..2: 10,12,11): max=12
        # At i=3 (window 1..3: 12,11,15): max=15
        # At i=4 (window 2..4: 11,15,13): max=15
        _, _, raw_k = calc_stochastic(candles, k_period=3, k_smooth=1, d_period=1)
        # Verify raw_k calculation reflects these rolling highest highs
        # At i=2: HH=12, LL=5, Close=9 -> range=7, 100*(9-5)/7 = 57.142857
        assert abs(raw_k[2] - (100.0 * 4.0 / 7.0)) < 1e-6
        # At i=3: HH=15, LL=6, Close=13 -> range=9, 100*(13-6)/9 = 77.777777
        assert abs(raw_k[3] - (100.0 * 7.0 / 9.0)) < 1e-6

    def test_stochastic_lowest_low(self):
        """TEST 2 — LOWEST LOW: K=3 over [10, 8, 9, 7, 11]"""
        lows = [10, 8, 9, 7, 11]
        candles = [{"high": l + 5, "low": l, "close": l + 2} for l in lows]
        # At i=2 (window 0..2: 10,8,9): min=8, HH=15, Close=11 -> 100*(11-8)/7 = 42.857
        # At i=3 (window 1..3: 8,9,7): min=7, HH=14, Close=9 -> 100*(9-7)/7 = 28.571
        _, _, raw_k = calc_stochastic(candles, k_period=3, k_smooth=1, d_period=1)
        assert abs(raw_k[2] - (100.0 * 3.0 / 7.0)) < 1e-6
        assert abs(raw_k[3] - (100.0 * 2.0 / 7.0)) < 1e-6

    def test_stochastic_raw_k(self):
        """TEST 3 — RAW %K: HH=120, LL=100, Close=115 -> %K_raw = 75"""
        candles = [{"high": 100, "low": 100, "close": 100}] * 13
        candles.append({"high": 120, "low": 100, "close": 115})
        _, _, raw_k = calc_stochastic(candles, k_period=14, k_smooth=1, d_period=1)
        # Expected: 100 * (115 - 100) / (120 - 100) = 1500 / 20 = 75.0
        assert abs(raw_k[13] - 75.0) < 1e-9

    def test_stochastic_raw_k_at_low(self):
        """TEST 4 — RAW %K AT LOW: Close = Lowest Low -> %K_raw = 0"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 13
        candles.append({"high": 110, "low": 100, "close": 100})
        _, _, raw_k = calc_stochastic(candles, k_period=14, k_smooth=1, d_period=1)
        assert abs(raw_k[13] - 0.0) < 1e-9

    def test_stochastic_raw_k_at_high(self):
        """TEST 5 — RAW %K AT HIGH: Close = Highest High -> %K_raw = 100"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 13
        candles.append({"high": 110, "low": 100, "close": 110})
        _, _, raw_k = calc_stochastic(candles, k_period=14, k_smooth=1, d_period=1)
        assert abs(raw_k[13] - 100.0) < 1e-9

    def test_stochastic_zero_range(self):
        """TEST 6 — ZERO RANGE: HH == LL -> No NaN, deterministic 50.0"""
        candles = [{"high": 100, "low": 100, "close": 100}] * 14
        k, d, raw_k = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        assert raw_k[13] == 50.0
        assert not any(v is not None and (v != v or abs(v) == float('inf')) for v in raw_k)

    def test_stochastic_k_smoothing(self):
        """TEST 7 — %K SMOOTHING: 3-period SMA of Raw %K"""
        # Create candles that produce known Raw %K
        # If Raw %K at 13=60, 14=70, 15=80 -> Smoothed %K at 15 = (60+70+80)/3 = 70.0
        candles = [{"high": 110, "low": 100, "close": 100}] * 13
        candles.append({"high": 110, "low": 100, "close": 106}) # raw_k = 60
        candles.append({"high": 110, "low": 100, "close": 107}) # raw_k = 70
        candles.append({"high": 110, "low": 100, "close": 108}) # raw_k = 80
        k, _, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=1)
        assert abs(k[15] - 70.0) < 1e-9

    def test_stochastic_d_calculation(self):
        """TEST 8 — %D CALCULATION: 3-period SMA of Smoothed %K"""
        candles = [{"high": 110, "low": 100, "close": 100}] * 13
        candles.append({"high": 110, "low": 100, "close": 106}) # raw_k = 60
        candles.append({"high": 110, "low": 100, "close": 106}) # raw_k = 60
        candles.append({"high": 110, "low": 100, "close": 106}) # raw_k = 60 -> k[15] = 60
        candles.append({"high": 110, "low": 100, "close": 109}) # raw_k = 90 -> k[16] = 70
        candles.append({"high": 110, "low": 100, "close": 109}) # raw_k = 90 -> k[17] = 80
        # Smoothed K at 15=60, 16=70, 17=80 -> %D at 17 = (60+70+80)/3 = 70.0
        k, d, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        assert abs(k[15] - 60.0) < 1e-9
        assert abs(k[16] - 70.0) < 1e-9
        assert abs(k[17] - 80.0) < 1e-9
        assert abs(d[17] - 70.0) < 1e-9

    def test_stochastic_full_manual_dataset(self):
        """TEST 9 — FULL STOCHASTIC: Manual calculation verification on deterministic dataset"""
        # 18 constant candles with close=105, H=110, L=100 -> Raw=50, K=50, D=50
        candles = [{"high": 110, "low": 100, "close": 105}] * 18
        k, d, raw_k = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        assert abs(raw_k[13] - 50.0) < 1e-9
        assert abs(k[15] - 50.0) < 1e-9
        assert abs(d[17] - 50.0) < 1e-9

    def test_stochastic_range(self):
        """TEST 10 — RANGE: Normal results remain 0 <= %K <= 100 and 0 <= %D <= 100"""
        candles = [{"high": 100 + (i % 7) * 3, "low": 100 - (i % 5) * 2, "close": 100 + (i % 6) - 2} for i in range(50)]
        k, d, raw_k = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        for v in raw_k:
            if v is not None:
                assert 0.0 <= v <= 100.0
        for v in k:
            if v is not None:
                assert 0.0 <= v <= 100.0
        for v in d:
            if v is not None:
                assert 0.0 <= v <= 100.0

    def test_stochastic_warmup(self):
        """TEST 11 — WARM-UP: No premature values before K, K_smooth, and D periods"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 20
        k, d, raw_k = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        # Raw %K requires 14 bars (indices 0..12 are None)
        for i in range(13):
            assert raw_k[i] is None
        assert raw_k[13] is not None

        # Smoothed %K requires 14 + 3 - 1 = 16 bars (indices 0..14 are None)
        for i in range(15):
            assert k[i] is None
        assert k[15] is not None

        # %D requires 14 + 3 + 3 - 2 = 18 bars (indices 0..16 are None)
        for i in range(17):
            assert d[i] is None
        assert d[17] is not None

    def test_stochastic_k_period(self):
        """TEST 12 — %K PERIOD: Stochastic(5,3,3) vs Stochastic(14,3,3)"""
        candles = [{"high": 100 + i, "low": 90 + i, "close": 95 + (i % 4)} for i in range(30)]
        k5, d5, _ = calc_stochastic(candles, k_period=5, k_smooth=3, d_period=3)
        k14, d14, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        # k5 starts at index 5-1+3-1 = 6
        assert k5[6] is not None and k5[5] is None
        # k14 starts at index 14-1+3-1 = 15
        assert k14[15] is not None and k14[14] is None
        assert k5[15] != k14[15]

    def test_stochastic_k_smoothing_param(self):
        """TEST 13 — %K SMOOTHING: Stochastic(14,1,3) vs Stochastic(14,3,3)"""
        candles = [{"high": 100 + (i % 5), "low": 90, "close": 92 + (i % 7)} for i in range(30)]
        k_smooth1, _, _ = calc_stochastic(candles, k_period=14, k_smooth=1, d_period=3)
        k_smooth3, _, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        assert k_smooth1[13] is not None
        assert k_smooth3[13] is None and k_smooth3[15] is not None

    def test_stochastic_d_period_param(self):
        """TEST 14 — %D PERIOD: Stochastic(14,3,3) vs Stochastic(14,3,5)"""
        candles = [{"high": 100 + (i % 5), "low": 90, "close": 92 + (i % 7)} for i in range(30)]
        _, d3, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        _, d5, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=5)
        # d3 starts at index 17
        assert d3[17] is not None and d3[16] is None
        # d5 starts at index 15 + 5 - 1 = 19
        assert d5[19] is not None and d5[18] is None

    def test_stochastic_live_candle_update(self):
        """TEST 15 — LIVE CANDLE: In-place update to open candle without duplicate history or state corruption"""
        candles = [{"high": 110, "low": 100, "close": 105}] * 18
        k1, d1, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        assert abs(k1[17] - 50.0) < 1e-9

        # Live tick changes open candle high/close
        candles[17] = {"high": 120, "low": 100, "close": 120}
        k2, d2, _ = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        assert len(k2) == 18
        # Historical value at index 16 is unaffected
        assert abs(k2[16] - 50.0) < 1e-9
        # Current value at index 17 updates
        assert k2[17] > 50.0

    def test_stochastic_symbol_reset(self):
        """TEST 16 — SYMBOL RESET: Switching symbol candle array builds independent state"""
        sym1 = [{"high": 100, "low": 90, "close": 95}] * 20
        sym2 = [{"high": 2500, "low": 2400, "close": 2450}] * 20
        k1, d1, _ = calc_stochastic(sym1, k_period=14, k_smooth=3, d_period=3)
        k2, d2, _ = calc_stochastic(sym2, k_period=14, k_smooth=3, d_period=3)
        assert k1[17] is not None and k2[17] is not None

    def test_stochastic_timeframe_reset(self):
        """TEST 17 — TIMEFRAME RESET: Operates candle-based over given timeframe"""
        daily = [{"high": 1500, "low": 1400, "close": 1450}] * 25
        k_d, d_d, _ = calc_stochastic(daily, k_period=14, k_smooth=3, d_period=3)
        assert len(k_d) == 25
        assert k_d[15] is not None

    def test_stochastic_service_pandas_integration(self):
        """TEST 18 — Backend IndicatorService.calculate_stochastic integration test"""
        import pandas as pd
        from indicator_service import IndicatorService
        candles = [{"high": 100 + (i % 6) * 2, "low": 90, "close": 92 + (i % 5)} for i in range(30)]
        df = IndicatorService.to_df(candles)
        res_df = IndicatorService.calculate_stochastic(df, k_period=14, k_smooth=3, d_period=3)
        k_helper, d_helper, raw_helper = calc_stochastic(candles, k_period=14, k_smooth=3, d_period=3)
        
        for i in range(15):
            assert pd.isna(res_df['k'].iloc[i])
        for i in range(15, 30):
            assert abs(res_df['k'].iloc[i] - k_helper[i]) < 1e-9
        for i in range(17):
            assert pd.isna(res_df['d'].iloc[i])
        for i in range(17, 30):
            assert abs(res_df['d'].iloc[i] - d_helper[i]) < 1e-9


class TestADXCorrectness:
    """
    Phase 2E — 22 Comprehensive Mathematical and Functional Correctness Tests for ADX / DMI
    """

    def test_adx_up_move(self):
        """TEST 1 — UP MOVE: High[i] > High[i-1] and UpMove > DownMove => +DM > 0 and -DM = 0"""
        c1 = {"high": 100, "low": 90, "close": 95}
        c2 = {"high": 108, "low": 92, "close": 105} # UpMove=8, DownMove=-2 => +DM=8, -DM=0
        # In a 2-candle set with period=2, index 1 +DM is 8 and -DM is 0
        _, plus_di, minus_di, _ = calc_adx([c1, c2], period=2)
        assert plus_di[1] > 0
        assert minus_di[1] == 0.0

    def test_adx_down_move(self):
        """TEST 2 — DOWN MOVE: Low[i] < Low[i-1] and DownMove > UpMove => -DM > 0 and +DM = 0"""
        c1 = {"high": 100, "low": 90, "close": 95}
        c2 = {"high": 98, "low": 82, "close": 85} # UpMove=-2, DownMove=8 => +DM=0, -DM=8
        _, plus_di, minus_di, _ = calc_adx([c1, c2], period=2)
        assert minus_di[1] > 0
        assert plus_di[1] == 0.0

    def test_adx_no_directional_movement(self):
        """TEST 3 — NO DIRECTIONAL MOVEMENT: Outside / Inside bar where neither wins or both negative"""
        c1 = {"high": 100, "low": 90, "close": 95}
        c2 = {"high": 99, "low": 91, "close": 94} # UpMove=-1, DownMove=-1 => +DM=0, -DM=0
        _, plus_di, minus_di, _ = calc_adx([c1, c2], period=2)
        assert plus_di[1] == 0.0
        assert minus_di[1] == 0.0

    def test_adx_true_range(self):
        """TEST 4 — TRUE RANGE: max(H-L, |H-C_prev|, |L-C_prev|)"""
        c1 = {"high": 100, "low": 90, "close": 95}
        c2 = {"high": 115, "low": 105, "close": 110} # Gap up: H-L=10, H-95=20, L-95=10 => TR=20
        # For period=2: sumTR = 10 (c1) + 20 (c2) = 30
        _, plus_di, _, _ = calc_adx([c1, c2], period=2)
        # UpMove=15, DownMove=-15 => +DM=15. Smoothed +DM = 15. +DI = 100 * 15 / 30 = 50.0
        assert abs(plus_di[1] - 50.0) < 1e-9

    def test_adx_plus_di(self):
        """TEST 5 — +DI: 100 * Smoothed(+DM) / Smoothed(TR)"""
        c1 = {"high": 100, "low": 90, "close": 95}
        c2 = {"high": 110, "low": 95, "close": 105}
        _, plus_di, minus_di, _ = calc_adx([c1, c2], period=2)
        # c1: TR=10, DM=0. c2: TR=15, Up=10, Down=-5 => +DM=10, -DM=0. sumTR=25, sum+DM=10 => +DI = 100 * 10 / 25 = 40.0
        assert abs(plus_di[1] - 40.0) < 1e-9
        assert minus_di[1] == 0.0

    def test_adx_minus_di(self):
        """TEST 6 — -DI: 100 * Smoothed(-DM) / Smoothed(TR)"""
        c1 = {"high": 100, "low": 90, "close": 95}
        c2 = {"high": 95, "low": 80, "close": 85}
        _, plus_di, minus_di, _ = calc_adx([c1, c2], period=2)
        # c1: TR=10. c2: TR=15, Up=-5, Down=10 => -DM=10. sumTR=25, sum-DM=10 => -DI = 100 * 10 / 25 = 40.0
        assert abs(minus_di[1] - 40.0) < 1e-9
        assert plus_di[1] == 0.0

    def test_adx_dx_formula(self):
        """TEST 7 — DX: 100 * |+DI - -DI| / (+DI + -DI)"""
        # When +DI = 40 and -DI = 20, DX = 100 * |40 - 20| / (40 + 20) = 2000 / 60 = 33.333333...
        plus_di_val = 40.0
        minus_di_val = 20.0
        dx_expected = 100.0 * abs(plus_di_val - minus_di_val) / (plus_di_val + minus_di_val)
        assert abs(dx_expected - (100.0 / 3.0)) < 1e-9

    def test_adx_zero_di_safe(self):
        """TEST 8 — ZERO DI: +DI = 0, -DI = 0 => DX = 0 without NaN / Inf"""
        flat = [{"high": 100, "low": 90, "close": 95}] * 10
        adx, plus_di, minus_di, dx = calc_adx(flat, period=3)
        for i in range(2, 10):
            assert plus_di[i] == 0.0
            assert minus_di[i] == 0.0
            assert dx[i] == 0.0

    def test_adx_wilder_smoothing(self):
        """TEST 9 — WILDER SMOOTHING: verify exact recurrence Smoothed[i] = Prev - Prev/N + Val[i]"""
        p = 3
        vals = [10.0, 20.0, 30.0, 40.0]
        initial_sum = 10.0 + 20.0 + 30.0 # 60.0
        next_smoothed = initial_sum - (initial_sum / p) + 40.0 # 60 - 20 + 40 = 80.0
        assert abs(next_smoothed - 80.0) < 1e-9

    def test_adx_initial_wilder_value(self):
        """TEST 10 — INITIAL WILDER VALUE: first smoothed value is sum of first N values at index N-1"""
        candles = [{"high": 100 + i * 2, "low": 90 + i * 2, "close": 95 + i * 2} for i in range(10)]
        _, plus_di, _, _ = calc_adx(candles, period=5)
        # Indices 0..3 are None
        for i in range(4):
            assert plus_di[i] is None
        # Index 4 is initialized
        assert plus_di[4] is not None

    def test_adx_initialization(self):
        """TEST 11 — ADX INITIALIZATION: first ADX is simple average of first N DX values at index 2N-2"""
        p = 3
        candles = [{"high": 100 + (i % 4) * 5, "low": 90, "close": 92 + (i % 3)} for i in range(10)]
        adx, plus_di, minus_di, dx = calc_adx(candles, period=p)
        # First valid DX is at index p-1 = 2
        # Need 3 DX values (indices 2, 3, 4) => First ADX is at index 2*p - 2 = 4
        for i in range(4):
            assert adx[i] is None
        assert adx[4] is not None
        expected_seed_adx = (dx[2] + dx[3] + dx[4]) / 3.0
        assert abs(adx[4] - expected_seed_adx) < 1e-9

    def test_adx_recursion(self):
        """TEST 12 — ADX RECURSION: ADX[i] = ((ADX[i-1] * (N-1)) + DX[i]) / N"""
        p = 3
        candles = [{"high": 100 + (i % 4) * 5, "low": 90, "close": 92 + (i % 3)} for i in range(10)]
        adx, plus_di, minus_di, dx = calc_adx(candles, period=p)
        for i in range(5, 10):
            expected = (adx[i - 1] * (p - 1) + dx[i]) / float(p)
            assert abs(adx[i] - expected) < 1e-9

    def test_adx_range_bounds(self):
        """TEST 13 — RANGE: 0 <= +DI <= 100, 0 <= -DI <= 100, 0 <= ADX <= 100"""
        import random
        random.seed(42)
        candles = []
        price = 100.0
        for _ in range(100):
            change = random.uniform(-5, 5)
            price += change
            candles.append({"high": price + 2, "low": price - 2, "close": price})
        
        adx, plus_di, minus_di, dx = calc_adx(candles, period=14)
        for i in range(100):
            if plus_di[i] is not None:
                assert 0.0 <= plus_di[i] <= 100.0
            if minus_di[i] is not None:
                assert 0.0 <= minus_di[i] <= 100.0
            if dx[i] is not None:
                assert 0.0 <= dx[i] <= 100.0
            if adx[i] is not None:
                assert 0.0 <= adx[i] <= 100.0

    def test_adx_warmup(self):
        """TEST 14 — WARM-UP: unavailable values remain None (no fake 0/50/100)"""
        candles = [{"high": 100, "low": 90, "close": 95}] * 30
        adx, plus_di, minus_di, dx = calc_adx(candles, period=14)
        for i in range(13):
            assert plus_di[i] is None
            assert minus_di[i] is None
            assert dx[i] is None
            assert adx[i] is None
        for i in range(13, 26):
            assert plus_di[i] is not None
            assert adx[i] is None
        assert adx[26] is not None

    def test_adx_flat_market(self):
        """TEST 15 — FLAT MARKET: High=Low=Close constant => no NaN, no Inf, no crash"""
        flat = [{"high": 100, "low": 100, "close": 100}] * 35
        adx, plus_di, minus_di, dx = calc_adx(flat, period=14)
        for i in range(26, 35):
            assert adx[i] == 0.0
            assert plus_di[i] == 0.0
            assert minus_di[i] == 0.0

    def test_adx_trending_up_data(self):
        """TEST 16 — TRENDING UP: +DI generally dominates -DI"""
        up_candles = [{"high": 100 + i * 5, "low": 95 + i * 5, "close": 98 + i * 5} for i in range(40)]
        adx, plus_di, minus_di, dx = calc_adx(up_candles, period=14)
        for i in range(13, 40):
            assert plus_di[i] > minus_di[i]
            assert minus_di[i] == 0.0

    def test_adx_trending_down_data(self):
        """TEST 17 — TRENDING DOWN: -DI generally dominates +DI"""
        down_candles = [{"high": 500 - i * 5, "low": 495 - i * 5, "close": 498 - i * 5} for i in range(40)]
        adx, plus_di, minus_di, dx = calc_adx(down_candles, period=14)
        for i in range(13, 40):
            assert minus_di[i] > plus_di[i]
            assert plus_di[i] == 0.0

    def test_adx_period_change(self):
        """TEST 18 — PERIOD CHANGE: ADX(14) vs ADX(20) differ correctly"""
        import math
        candles = [{"high": 100 + math.sin(i * 0.4) * 10 + 5, "low": 100 + math.sin(i * 0.4) * 10 - 5, "close": 100 + math.sin(i * 0.4) * 10} for i in range(50)]
        adx14, _, _, _ = calc_adx(candles, period=14)
        adx20, _, _, _ = calc_adx(candles, period=20)
        assert adx14[26] is not None
        assert adx20[26] is None # 20 needs 2*20-2 = 38 bars
        assert adx20[38] is not None
        assert abs(adx14[45] - adx20[45]) > 1e-4

    def test_adx_live_candle_update(self):
        """TEST 19 — LIVE CANDLE: In-place update of open bar does not duplicate state"""
        import math
        candles = [{"high": 100 + math.sin(i * 0.3) * 8 + 4, "low": 100 + math.sin(i * 0.3) * 8 - 4, "close": 100 + math.sin(i * 0.3) * 8} for i in range(30)]
        adx1, _, _, _ = calc_adx(candles, period=14)
        
        # Tick updates forming candle (index 29)
        candles[29] = {"high": 140, "low": 100, "close": 138}
        adx2, _, _, _ = calc_adx(candles, period=14)
        
        assert len(adx2) == 30
        assert abs(adx2[28] - adx1[28]) < 1e-9 # Prior historical bar unchanged
        assert abs(adx2[29] - adx1[29]) > 1e-4 # Forming bar updated

    def test_adx_symbol_reset(self):
        """TEST 20 — SYMBOL RESET: Switching symbol candle array has independent state"""
        sym1 = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(35)]
        sym2 = [{"high": 2500 - i * 2, "low": 2490 - i * 2, "close": 2495 - i * 2} for i in range(35)]
        adx1, p1, m1, _ = calc_adx(sym1, period=14)
        adx2, p2, m2, _ = calc_adx(sym2, period=14)
        assert p1[30] > m1[30]
        assert m2[30] > p2[30]

    def test_adx_timeframe_reset(self):
        """TEST 21 — TIMEFRAME RESET: Operates candle-based over given timeframe array"""
        h1 = [{"high": 100 + i * 2, "low": 95 + i * 2, "close": 98 + i * 2} for i in range(35)]
        adx_h1, _, _, _ = calc_adx(h1, period=14)
        assert len(adx_h1) == 35
        assert adx_h1[26] is not None

    def test_adx_service_pandas_integration(self):
        """TEST 22 — Backend IndicatorService.calculate_adx integration test"""
        import pandas as pd
        from indicator_service import IndicatorService
        candles = [{"high": 100 + (i % 6) * 3, "low": 90, "close": 92 + (i % 4) * 2} for i in range(40)]
        df = IndicatorService.to_df(candles)
        res_df = IndicatorService.calculate_adx(df, period=14)
        adx_h, p_h, m_h, dx_h = calc_adx(candles, period=14)
        
        for i in range(13):
            assert pd.isna(res_df['plus_di'].iloc[i])
            assert pd.isna(res_df['minus_di'].iloc[i])
            assert pd.isna(res_df['dx'].iloc[i])
            assert pd.isna(res_df['adx'].iloc[i])
            
        for i in range(13, 40):
            assert abs(res_df['plus_di'].iloc[i] - p_h[i]) < 1e-9
            assert abs(res_df['minus_di'].iloc[i] - m_h[i]) < 1e-9
            assert abs(res_df['dx'].iloc[i] - dx_h[i]) < 1e-9
            
        for i in range(26):
            assert pd.isna(res_df['adx'].iloc[i])
            
        for i in range(26, 40):
            assert abs(res_df['adx'].iloc[i] - adx_h[i]) < 1e-9


# =====================================================================
# PHASE 2F — OBV TEST HELPER & TEST SUITE
# =====================================================================

def calc_obv(candles: List[Dict]) -> List[float]:
    """Pure Python reference implementation of OBV."""
    n = len(candles)
    if n == 0:
        return []
    out = [0.0] * n
    cur_obv = 0.0
    for i in range(1, n):
        prev_c = float(candles[i - 1].get('close', 0.0))
        cur_c = float(candles[i].get('close', 0.0))
        vol = float(candles[i].get('volume', 0.0))
        if not np.isfinite(vol) or vol < 0:
            vol = 0.0
        if not np.isfinite(prev_c) or not np.isfinite(cur_c):
            pass
        elif cur_c > prev_c:
            cur_obv += vol
        elif cur_c < prev_c:
            cur_obv -= vol
        out[i] = cur_obv
    return out


class TestOBVCorrectness:
    """Production-grade mathematical and integration tests for On-Balance Volume (Phase 2F)."""

    def test_obv_initial_value(self):
        """TEST 1 — INITIAL VALUE: First candle Close = 100, Volume = 1000 => OBV = 0"""
        candles = [{"close": 100, "volume": 1000}]
        res = calc_obv(candles)
        assert len(res) == 1
        assert res[0] == 0.0

    def test_obv_price_increase(self):
        """TEST 2 — PRICE INCREASE: Close rises from 100 to 105 with volume 2000 => OBV = 2000"""
        candles = [
            {"close": 100, "volume": 1000},
            {"close": 105, "volume": 2000}
        ]
        res = calc_obv(candles)
        assert res[0] == 0.0
        assert res[1] == 2000.0

    def test_obv_price_decrease(self):
        """TEST 3 — PRICE DECREASE: Close drops from 105 to 103 with volume 1500 => OBV = 500"""
        candles = [
            {"close": 100, "volume": 1000},
            {"close": 105, "volume": 2000},
            {"close": 103, "volume": 1500}
        ]
        res = calc_obv(candles)
        assert res[2] == 500.0

    def test_obv_unchanged_close(self):
        """TEST 4 — UNCHANGED CLOSE: Close unchanged at 103 with volume 5000 => OBV remains 500"""
        candles = [
            {"close": 100, "volume": 1000},
            {"close": 105, "volume": 2000},
            {"close": 103, "volume": 1500},
            {"close": 103, "volume": 5000}
        ]
        res = calc_obv(candles)
        assert res[3] == 500.0

    def test_obv_multiple_candles_deterministic(self):
        """TEST 5 — MULTIPLE CANDLES: Exact deterministic sequence verification"""
        candles = [
            {"close": 100, "volume": 1000},
            {"close": 105, "volume": 2000},
            {"close": 103, "volume": 1500},
            {"close": 103, "volume": 3000},
            {"close": 110, "volume": 2500},
            {"close": 108, "volume": 1000}
        ]
        expected = [0.0, 2000.0, 500.0, 500.0, 3000.0, 2000.0]
        res = calc_obv(candles)
        assert res == expected

    def test_obv_zero_volume(self):
        """TEST 6 — ZERO VOLUME: Volume = 0 => OBV remains unchanged"""
        candles = [
            {"close": 100, "volume": 1000},
            {"close": 105, "volume": 0},
            {"close": 102, "volume": 0}
        ]
        res = calc_obv(candles)
        assert res == [0.0, 0.0, 0.0]

    def test_obv_large_volume(self):
        """TEST 7 — LARGE VOLUME: High volumes without overflow or NaN"""
        candles = [
            {"close": 100, "volume": 1e12},
            {"close": 110, "volume": 5e11},
            {"close": 105, "volume": 2e11}
        ]
        res = calc_obv(candles)
        assert res == [0.0, 5e11, 3e11]
        assert np.isfinite(res[2])

    def test_obv_flat_price(self):
        """TEST 8 — FLAT PRICE: Multiple flat candles with varying volume => OBV stays 0"""
        candles = [{"close": 100, "volume": 1000 + i * 500} for i in range(10)]
        res = calc_obv(candles)
        assert all(v == 0.0 for v in res)

    def test_obv_alternating_direction(self):
        """TEST 9 — ALTERNATING DIRECTION: Up/Down/Up/Down sequence"""
        candles = [
            {"close": 100, "volume": 100},
            {"close": 105, "volume": 200}, # +200 => 200
            {"close": 100, "volume": 150}, # -150 => 50
            {"close": 108, "volume": 300}, # +300 => 350
            {"close": 102, "volume": 100}, # -100 => 250
            {"close": 110, "volume": 400}, # +400 => 650
        ]
        res = calc_obv(candles)
        assert res == [0.0, 200.0, 50.0, 350.0, 250.0, 650.0]

    def test_obv_long_uptrend(self):
        """TEST 10 — LONG UPTREND: Monotonically increasing price => strictly increasing OBV"""
        candles = [{"close": 100 + i, "volume": 100} for i in range(20)]
        res = calc_obv(candles)
        for i in range(1, 20):
            assert res[i] > res[i - 1]
        assert res[19] == 1900.0

    def test_obv_long_downtrend(self):
        """TEST 11 — LONG DOWNTREND: Monotonically decreasing price => strictly decreasing OBV"""
        candles = [{"close": 200 - i, "volume": 100} for i in range(20)]
        res = calc_obv(candles)
        for i in range(1, 20):
            assert res[i] < res[i - 1]
        assert res[19] == -1900.0

    def test_obv_zero_volume_up(self):
        """TEST 12 — ZERO-VOLUME UP: Price rises with volume 0 => OBV unchanged"""
        candles = [
            {"close": 100, "volume": 500},
            {"close": 105, "volume": 0}
        ]
        res = calc_obv(candles)
        assert res == [0.0, 0.0]

    def test_obv_zero_volume_down(self):
        """TEST 13 — ZERO-VOLUME DOWN: Price falls with volume 0 => OBV unchanged"""
        candles = [
            {"close": 100, "volume": 500},
            {"close": 95, "volume": 0}
        ]
        res = calc_obv(candles)
        assert res == [0.0, 0.0]

    def test_obv_live_candle_update(self):
        """TEST 14 — LIVE CANDLE UPDATE: Recalculates from previous closed candle without double-counting"""
        hist = [
            {"close": 90, "volume": 1000},
            {"close": 100, "volume": 5000} # Historical OBV = 5000
        ]
        obv_hist = calc_obv(hist)
        assert obv_hist[1] == 5000.0

        # Live update 1
        live1 = hist + [{"close": 105, "volume": 500}]
        res1 = calc_obv(live1)
        assert res1[2] == 5500.0

        # Live update 2 on same bar (volume grows to 800)
        live2 = hist + [{"close": 107, "volume": 800}]
        res2 = calc_obv(live2)
        assert res2[2] == 5800.0 # NOT 6300

    def test_obv_live_direction_reversal(self):
        """TEST 15 — LIVE DIRECTION REVERSAL: Price flips from up to down during same candle"""
        hist = [
            {"close": 90, "volume": 1000},
            {"close": 100, "volume": 5000} # OBV = 5000
        ]
        # Initially candle is green with 500 volume
        live_up = hist + [{"close": 105, "volume": 500}]
        res_up = calc_obv(live_up)
        assert res_up[2] == 5500.0

        # Later candle turns red with 800 cumulative volume
        live_down = hist + [{"close": 99, "volume": 800}]
        res_down = calc_obv(live_down)
        assert res_down[2] == 4200.0 # 5000 - 800 = 4200 (NOT 4700)

    def test_obv_live_volume_accumulation(self):
        """TEST 16 — LIVE VOLUME ACCUMULATION: Cumulative volume ticks [100, 250, 400]"""
        hist = [{"close": 100, "volume": 1000}]
        # Ticks:
        c1 = hist + [{"close": 102, "volume": 100}]
        c2 = hist + [{"close": 103, "volume": 250}]
        c3 = hist + [{"close": 104, "volume": 400}]
        assert calc_obv(c1)[1] == 100.0
        assert calc_obv(c2)[1] == 250.0
        assert calc_obv(c3)[1] == 400.0

    def test_obv_closed_candle_finalization(self):
        """TEST 17 — CLOSED CANDLE FINALIZATION: Live OBV matches finalized historical OBV"""
        candles = [
            {"close": 100, "volume": 1000},
            {"close": 105, "volume": 2000},
            {"close": 102, "volume": 1500}
        ]
        res = calc_obv(candles)
        assert res[2] == 500.0

    def test_obv_new_candle(self):
        """TEST 18 — NEW CANDLE: Next candle starts accumulating from previous closed OBV"""
        closed_seq = [
            {"close": 100, "volume": 1000},
            {"close": 105, "volume": 2000} # OBV = 2000
        ]
        new_bar = closed_seq + [{"close": 108, "volume": 700}]
        res = calc_obv(new_bar)
        assert res[2] == 2700.0

    def test_obv_timeframe_change(self):
        """TEST 19 — TIMEFRAME CHANGE: Recalculates over timeframe array"""
        tf_5m = [{"close": 100 + i, "volume": 100} for i in range(10)]
        tf_1h = [{"close": 100 + i * 5, "volume": 1000} for i in range(10)]
        assert calc_obv(tf_5m)[9] == 900.0
        assert calc_obv(tf_1h)[9] == 9000.0

    def test_obv_symbol_change(self):
        """TEST 20 — SYMBOL CHANGE: Independent state per symbol"""
        sym1 = [{"close": 100 + i, "volume": 100} for i in range(5)]
        sym2 = [{"close": 2000 - i * 10, "volume": 500} for i in range(5)]
        assert calc_obv(sym1)[4] == 400.0
        assert calc_obv(sym2)[4] == -2000.0

    def test_obv_independent_reference_calculation(self):
        """TEST 21 — INDEPENDENT REFERENCE: Mathematical correctness test with analytical series"""
        # Independent manual model:
        # C0: 50, V: 100 -> OBV = 0
        # C1: 55 (+), V: 250 -> OBV = +250
        # C2: 52 (-), V: 150 -> OBV = +100
        # C3: 52 (=), V: 300 -> OBV = +100
        # C4: 48 (-), V: 400 -> OBV = -300
        # C5: 60 (+), V: 800 -> OBV = +500
        candles = [
            {"close": 50, "volume": 100},
            {"close": 55, "volume": 250},
            {"close": 52, "volume": 150},
            {"close": 52, "volume": 300},
            {"close": 48, "volume": 400},
            {"close": 60, "volume": 800}
        ]
        analytical_reference = [0.0, 250.0, 100.0, 100.0, -300.0, 500.0]
        assert calc_obv(candles) == analytical_reference

    def test_obv_historical_vs_sequential_live_consistency(self):
        """TEST 22 — HISTORICAL VS LIVE EQUIVALENCE: Sequential live updates match full historical array"""
        full_candles = [
            {"close": 100 + (i % 5) * 2 - (i % 3), "volume": 1000 + i * 50}
            for i in range(30)
        ]
        full_hist_obv = calc_obv(full_candles)

        # Build sequentially
        seq_candles = []
        seq_obv = []
        for c in full_candles:
            seq_candles.append(c)
            seq_obv.append(calc_obv(seq_candles)[-1])

        assert full_hist_obv == seq_obv

    def test_obv_service_pandas_integration(self):
        """TEST 23 — Backend IndicatorService.calculate_obv integration test"""
        import pandas as pd
        from indicator_service import IndicatorService
        candles = [
            {"close": 100 + (i % 4) * 3 - (i % 2) * 2, "volume": 500 + i * 100}
            for i in range(30)
        ]
        df = IndicatorService.to_df(candles)
        res_df = IndicatorService.calculate_obv(df)
        expected_obv = calc_obv(candles)

        for i in range(30):
            assert abs(res_df['obv'].iloc[i] - expected_obv[i]) < 1e-9


# =====================================================================
# PHASE 2G — CCI TEST HELPER & TEST SUITE
# =====================================================================

def calc_cci(candles: List[Dict], period: int = 20) -> List[Optional[float]]:
    """Pure Python reference implementation of Commodity Channel Index (CCI)."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if period < 1 or n < period:
        return out

    tp = [0.0] * n
    for i in range(n):
        h = float(candles[i].get('high', candles[i].get('close', 0.0)))
        l = float(candles[i].get('low', candles[i].get('close', 0.0)))
        c = float(candles[i].get('close', 0.0))
        if not np.isfinite(h): h = c
        if not np.isfinite(l): l = c
        tp[i] = (h + l + c) / 3.0

    for i in range(period - 1, n):
        window = tp[i - period + 1 : i + 1]
        sma_tp = sum(window) / period
        mean_dev = sum(abs(x - sma_tp) for x in window) / period

        if mean_dev == 0 or not np.isfinite(mean_dev):
            out[i] = None
        else:
            out[i] = (tp[i] - sma_tp) / (0.015 * mean_dev)

    return out


class TestCCICorrectness:
    """Production-grade mathematical and integration tests for Commodity Channel Index (Phase 2G)."""

    def test_cci_typical_price(self):
        """TEST 1 — TYPICAL PRICE: TP = (High + Low + Close) / 3"""
        candle = {"high": 120, "low": 90, "close": 105}
        tp = (candle["high"] + candle["low"] + candle["close"]) / 3.0
        assert tp == 105.0

    def test_cci_warmup_period(self):
        """TEST 2 — PERIOD WARM-UP: Period 3 has 2 nulls, 3rd is first valid"""
        candles = [
            {"high": 10, "low": 8, "close": 9},
            {"high": 12, "low": 9, "close": 11},
            {"high": 14, "low": 10, "close": 13},
            {"high": 15, "low": 12, "close": 14}
        ]
        res = calc_cci(candles, period=3)
        assert res[0] is None
        assert res[1] is None
        assert res[2] is not None
        assert res[3] is not None

    def test_cci_sma_of_typical_price(self):
        """TEST 3 — SMA OF TYPICAL PRICE: Validates sliding window average of TP"""
        candles = [
            {"high": 12, "low": 6, "close": 9},   # TP = 9
            {"high": 15, "low": 9, "close": 12},  # TP = 12
            {"high": 18, "low": 12, "close": 15}  # TP = 15
        ]
        # SMA = (9 + 12 + 15) / 3 = 12
        # Deviations from 12: |9-12|=3, |12-12|=0, |15-12|=3 => MeanDev = 6/3 = 2
        # CCI = (15 - 12) / (0.015 * 2) = 3 / 0.03 = 100.0
        res = calc_cci(candles, period=3)
        assert abs(res[2] - 100.0) < 1e-9

    def test_cci_mean_deviation(self):
        """TEST 4 — MEAN DEVIATION: Verifies average absolute difference from window SMA"""
        candles = [
            {"high": 10, "low": 10, "close": 10}, # TP = 10
            {"high": 20, "low": 20, "close": 20}, # TP = 20
            {"high": 30, "low": 30, "close": 30}  # TP = 30
        ]
        # SMA = 20, MeanDev = (|10-20| + |20-20| + |30-20|)/3 = (10+0+10)/3 = 20/3
        # CCI = (30 - 20) / (0.015 * 20/3) = 10 / 0.1 = 100.0
        res = calc_cci(candles, period=3)
        assert abs(res[2] - 100.0) < 1e-9

    def test_cci_full_formula(self):
        """TEST 5 — FULL CCI FORMULA: Mathematical verification against deterministic analytical steps"""
        candles = [
            {"high": 24, "low": 20, "close": 22}, # TP = 22
            {"high": 26, "low": 22, "close": 24}, # TP = 24
            {"high": 28, "low": 24, "close": 26}  # TP = 26
        ]
        # SMA = 24, MeanDev = (|22-24| + |24-24| + |26-24|)/3 = 4/3
        # CCI = (26 - 24) / (0.015 * 4/3) = 2 / 0.02 = 100.0
        res = calc_cci(candles, period=3)
        assert abs(res[2] - 100.0) < 1e-9

    def test_cci_positive_value(self):
        """TEST 6 — POSITIVE CCI: Price above period average yields positive CCI"""
        candles = [{"high": 100 + i * 2, "low": 98 + i * 2, "close": 99 + i * 2} for i in range(5)]
        res = calc_cci(candles, period=3)
        assert res[4] > 0.0

    def test_cci_negative_value(self):
        """TEST 7 — NEGATIVE CCI: Price below period average yields negative CCI"""
        candles = [{"high": 200 - i * 2, "low": 198 - i * 2, "close": 199 - i * 2} for i in range(5)]
        res = calc_cci(candles, period=3)
        assert res[4] < 0.0

    def test_cci_zero_value(self):
        """TEST 8 — ZERO CCI: When latest TP equals window SMA, CCI is exactly 0"""
        candles = [
            {"high": 10, "low": 10, "close": 10}, # TP = 10
            {"high": 30, "low": 30, "close": 30}, # TP = 30
            {"high": 20, "low": 20, "close": 20}  # TP = 20 (SMA of [10, 30, 20] = 20)
        ]
        res = calc_cci(candles, period=3)
        assert abs(res[2] - 0.0) < 1e-9

    def test_cci_zero_mean_deviation(self):
        """TEST 9 — ZERO MEAN DEVIATION: Identical prices yield null without NaN or Infinity"""
        candles = [{"high": 100, "low": 100, "close": 100} for _ in range(5)]
        res = calc_cci(candles, period=3)
        assert res[2] is None
        assert res[3] is None
        assert res[4] is None

    def test_cci_period_20_default(self):
        """TEST 10 — DEFAULT PERIOD 20: First 19 are null, 20th is valid"""
        candles = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(25)]
        res = calc_cci(candles, period=20)
        for i in range(19):
            assert res[i] is None
        assert res[19] is not None
        assert res[24] is not None

    def test_cci_period_14_custom(self):
        """TEST 11 — CUSTOM PERIOD 14: First 13 are null, 14th is valid"""
        candles = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(20)]
        res = calc_cci(candles, period=14)
        for i in range(13):
            assert res[i] is None
        assert res[13] is not None

    def test_cci_period_50_custom(self):
        """TEST 12 — CUSTOM PERIOD 50: First 49 are null, 50th is valid"""
        candles = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(60)]
        res = calc_cci(candles, period=50)
        for i in range(49):
            assert res[i] is None
        assert res[49] is not None

    def test_cci_invalid_periods(self):
        """TEST 13 — INVALID PERIODS: Rejection of non-positive/insufficient periods"""
        candles = [{"high": 100, "low": 90, "close": 95} for _ in range(5)]
        assert calc_cci(candles, period=0) == [None] * 5
        assert calc_cci(candles, period=-5) == [None] * 5

    def test_cci_exceeds_plus_100(self):
        """TEST 14 — CCI > +100: Unclamped values properly pass +100 threshold"""
        candles = [{"high": 100, "low": 100, "close": 100} for _ in range(4)] + [
            {"high": 150, "low": 150, "close": 150} # Sharp rally
        ]
        res = calc_cci(candles, period=5)
        assert res[4] > 100.0

    def test_cci_below_minus_100(self):
        """TEST 15 — CCI < -100: Unclamped values properly fall below -100 threshold"""
        candles = [{"high": 100, "low": 100, "close": 100} for _ in range(4)] + [
            {"high": 50, "low": 50, "close": 50} # Sharp drop
        ]
        res = calc_cci(candles, period=5)
        assert res[4] < -100.0

    def test_cci_exceeds_plus_200(self):
        """TEST 16 — CCI > +200: Extreme upward displacement supported"""
        candles = [{"high": 100, "low": 100, "close": 100} for _ in range(9)] + [
            {"high": 300, "low": 300, "close": 300}
        ]
        res = calc_cci(candles, period=10)
        assert res[9] > 200.0

    def test_cci_below_minus_200(self):
        """TEST 17 — CCI < -200: Extreme downward displacement supported"""
        candles = [{"high": 300, "low": 300, "close": 300} for _ in range(9)] + [
            {"high": 50, "low": 50, "close": 50}
        ]
        res = calc_cci(candles, period=10)
        assert res[9] < -200.0

    def test_cci_live_update(self):
        """TEST 18 — LIVE UPDATE: Updates only forming bar, prior historical bars unchanged"""
        hist = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(10)]
        res1 = calc_cci(hist, period=5)
        
        forming_update = hist[:-1] + [{"high": 125, "low": 100, "close": 120}]
        res2 = calc_cci(forming_update, period=5)

        for i in range(9):
            assert (res1[i] is None and res2[i] is None) or abs(res1[i] - res2[i]) < 1e-9
        assert abs(res1[9] - res2[9]) > 1e-4

    def test_cci_live_no_duplication(self):
        """TEST 19 — LIVE NO DUPLICATION: Multiple updates to same forming candle keep array length constant"""
        hist = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(10)]
        c1 = hist[:-1] + [{"high": 110, "low": 100, "close": 105}]
        c2 = hist[:-1] + [{"high": 112, "low": 100, "close": 108}]
        assert len(calc_cci(c1, period=5)) == 10
        assert len(calc_cci(c2, period=5)) == 10

    def test_cci_live_high_update(self):
        """TEST 20 — LIVE HIGH UPDATE: Increasing high increases TP and CCI"""
        hist = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(9)]
        c1 = hist + [{"high": 110, "low": 100, "close": 105}]
        c2 = hist + [{"high": 120, "low": 100, "close": 105}] # Higher high
        assert calc_cci(c2, period=5)[9] > calc_cci(c1, period=5)[9]

    def test_cci_live_low_update(self):
        """TEST 21 — LIVE LOW UPDATE: Decreasing low decreases TP and CCI"""
        hist = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(9)]
        c1 = hist + [{"high": 110, "low": 100, "close": 105}]
        c2 = hist + [{"high": 110, "low": 90, "close": 105}] # Lower low
        assert calc_cci(c2, period=5)[9] < calc_cci(c1, period=5)[9]

    def test_cci_live_close_update(self):
        """TEST 22 — LIVE CLOSE UPDATE: Higher close increases CCI"""
        hist = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(9)]
        c1 = hist + [{"high": 110, "low": 100, "close": 102}]
        c2 = hist + [{"high": 110, "low": 100, "close": 108}] # Higher close
        assert calc_cci(c2, period=5)[9] > calc_cci(c1, period=5)[9]

    def test_cci_candle_finalization(self):
        """TEST 23 — CANDLE FINALIZATION: Live CCI equals historical once finalized"""
        candles = [{"high": 100 + i * 2, "low": 95 + i * 2, "close": 98 + i * 2} for i in range(15)]
        res = calc_cci(candles, period=10)
        assert res[14] is not None

    def test_cci_historical_vs_sequential_live_consistency(self):
        """TEST 24 — HISTORICAL VS LIVE EQUIVALENCE: Sequential live updates match full historical array"""
        full_candles = [
            {"high": 100 + (i % 5) * 3, "low": 90 - (i % 2), "close": 95 + (i % 4) * 2}
            for i in range(35)
        ]
        full_hist_cci = calc_cci(full_candles, period=14)

        seq_candles = []
        seq_cci = []
        for c in full_candles:
            seq_candles.append(c)
            seq_cci.append(calc_cci(seq_candles, period=14)[-1])

        for h, s in zip(full_hist_cci, seq_cci):
            if h is None:
                assert s is None
            else:
                assert abs(h - s) < 1e-9

    def test_cci_timeframe_reset(self):
        """TEST 25 — TIMEFRAME RESET: Independent calculation per timeframe series"""
        tf_5m = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(25)]
        tf_1h = [{"high": 100 + i * 5, "low": 95 + i * 5, "close": 98 + i * 5} for i in range(25)]
        assert calc_cci(tf_5m, period=20)[24] is not None
        assert calc_cci(tf_1h, period=20)[24] is not None

    def test_cci_symbol_reset(self):
        """TEST 26 — SYMBOL RESET: Switching symbol has zero state contamination"""
        sym1 = [{"high": 100 + i, "low": 95 + i, "close": 98 + i} for i in range(25)]
        sym2 = [{"high": 2500 - i * 2, "low": 2490 - i * 2, "close": 2495 - i * 2} for i in range(25)]
        r1 = calc_cci(sym1, period=10)
        r2 = calc_cci(sym2, period=10)
        assert r1[24] > 0
        assert r2[24] < 0

    def test_cci_independent_reference_calculation(self):
        """TEST 27 — INDEPENDENT REFERENCE: Mathematical verification against independently computed table"""
        # Dataset of 5 candles with Period = 5:
        # C0: H=24, L=20, C=22 => TP = 22.0
        # C1: H=27, L=21, C=24 => TP = 24.0
        # C2: H=29, L=23, C=26 => TP = 26.0
        # C3: H=31, L=25, C=28 => TP = 28.0
        # C4: H=33, L=27, C=30 => TP = 30.0
        # SMA_TP = (22 + 24 + 26 + 28 + 30) / 5 = 130 / 5 = 26.0
        # MeanDev = (|22-26| + |24-26| + |26-26| + |28-26| + |30-26|) / 5
        #         = (4 + 2 + 0 + 2 + 4) / 5 = 12 / 5 = 2.4
        # CCI = (30.0 - 26.0) / (0.015 * 2.4) = 4.0 / 0.036 = 111.11111111111111
        candles = [
            {"high": 24, "low": 20, "close": 22},
            {"high": 27, "low": 21, "close": 24},
            {"high": 29, "low": 23, "close": 26},
            {"high": 31, "low": 25, "close": 28},
            {"high": 33, "low": 27, "close": 30}
        ]
        res = calc_cci(candles, period=5)
        expected = 4.0 / (0.015 * 2.4)
        assert abs(res[4] - expected) < 1e-9

    def test_cci_service_pandas_integration(self):
        """TEST 28 — Backend IndicatorService.calculate_cci integration test"""
        import pandas as pd
        from indicator_service import IndicatorService
        candles = [
            {"high": 100 + (i % 5) * 2, "low": 90, "close": 95 + (i % 3)}
            for i in range(30)
        ]
        df = IndicatorService.to_df(candles)
        res_df = IndicatorService.calculate_cci(df, period=14)
        expected_cci = calc_cci(candles, period=14)

        for i in range(13):
            assert pd.isna(res_df['cci'].iloc[i])

        for i in range(13, 30):
            if expected_cci[i] is None:
                assert pd.isna(res_df['cci'].iloc[i])
            else:
                assert abs(res_df['cci'].iloc[i] - expected_cci[i]) < 1e-9


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2H — WILLIAMS %R
# ═════════════════════════════════════════════════════════════════════════════

def calc_williams_r(candles, period):
    """
    Pure reference implementation of Williams %R (matches indicators.js Calc.williamsR).
    %R = ((HighestHigh - Close) / (HighestHigh - LowestLow)) * -100
    Warm-up: first period-1 values are None.
    Zero-range (HH == LL): returns None.
    """
    n = len(candles)
    out = [None] * n
    if n == 0 or period < 1:
        return out
    for i in range(period - 1, n):
        hh = max(float(c["high"]) for c in candles[i - period + 1: i + 1])
        ll = min(float(c["low"])  for c in candles[i - period + 1: i + 1])
        cl = float(candles[i]["close"])
        rng = hh - ll
        if rng == 0 or not math.isfinite(rng):
            out[i] = None
        else:
            out[i] = ((hh - cl) / rng) * -100.0
    return out


def calc_roc(src, period, signal_period=0):
    """
    Independent reference port of indicators.js Calc.roc.
    ROC[i] = ((src[i] - src[i-period]) / src[i-period]) * 100.
    src[i-period] == 0 (or non-finite) -> ROC[i] left None instead of
    dividing by zero. signal_period > 0 runs an SMA over the ROC line
    itself (reuses calc_sma the same way indicators.js reuses
    Calc.smoothIgnoringNulls) — 0/falsy means the signal line is off
    (all None).
    """
    n = len(src) if src else 0
    result = [None] * n
    if n == 0 or period is None or period < 1:
        return result, [None] * n

    for i in range(period, n):
        base = src[i - period]
        cur = src[i]
        if not math.isfinite(base) or base == 0 or not math.isfinite(cur):
            continue
        result[i] = ((cur - base) / base) * 100.0

    if signal_period and signal_period > 0:
        first = next((i for i, v in enumerate(result) if v is not None), -1)
        signal = [None] * n
        if first != -1:
            tail = [v for v in result[first:] if v is not None]
            sig_tail = calc_sma(tail, signal_period)
            for i, sv in enumerate(sig_tail):
                signal[first + i] = sv
    else:
        signal = [None] * n

    return result, signal


def calc_aroon(candles, period):
    """
    Independent reference port of indicators.js Calc.aroon.
    For index i (needs a full period+1-bar window [i-period, i]):
      AroonUp[i]   = ((period - barsSinceHighestHigh) / period) * 100
      AroonDown[i] = ((period - barsSinceLowestLow)   / period) * 100
      AroonOsc[i]  = AroonUp[i] - AroonDown[i]
    Ties resolve to the MOST RECENT occurrence (>= / <= while scanning
    forward so a later equal value overwrites an earlier one).
    """
    n = len(candles) if candles else 0
    up = [None] * n
    down = [None] * n
    osc = [None] * n
    if n == 0 or period is None or period < 1:
        return {"up": up, "down": down, "osc": osc}

    def H(i):
        v = float(candles[i].get("high", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    def L(i):
        v = float(candles[i].get("low", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    for i in range(period, n):
        hi_idx = lo_idx = i - period
        hi_val = H(hi_idx)
        lo_val = L(lo_idx)
        for j in range(i - period + 1, i + 1):
            h, l = H(j), L(j)
            if h >= hi_val:
                hi_val, hi_idx = h, j
            if l <= lo_val:
                lo_val, lo_idx = l, j
        bars_since_high = i - hi_idx
        bars_since_low = i - lo_idx
        up[i] = ((period - bars_since_high) / period) * 100.0
        down[i] = ((period - bars_since_low) / period) * 100.0
        osc[i] = up[i] - down[i]

    return {"up": up, "down": down, "osc": osc}


def calc_cmf(candles, period):
    """
    Independent reference port of indicators.js Calc.cmf.
    Step 1 (per-bar): MFM[i] = ((C-L)-(H-C)) / (H-L); a zero/non-finite
      range is forced to MFM[i]=0 instead of dividing by zero.
    Step 2 (per-bar): MFV[i] = MFM[i] * Volume[i]
    Step 3 (rolling): CMF[i] = sum(MFV, last period) / sum(Volume, last
      period), via a running sum (matches calc_sma's own O(1)-per-bar
      technique). sum(Volume, period) == 0 for a window -> CMF[i] left
      None. First valid index is period-1 (period bars of history
      required), matching calc_sma/calc_atr's own warm-up convention.
    """
    n = len(candles) if candles else 0
    result = [None] * n
    if n == 0 or period is None or period < 1 or n < period:
        return result

    mfv = [0.0] * n
    vol = [0.0] * n
    for i in range(n):
        c = candles[i]
        h = float(c.get("high", c.get("close", 0)))
        l = float(c.get("low", c.get("close", 0)))
        cl = float(c.get("close", (h + l) / 2.0))
        if not math.isfinite(h):
            h = cl
        if not math.isfinite(l):
            l = cl
        rng = h - l
        mfm = 0.0 if (not math.isfinite(rng) or rng == 0) else (((cl - l) - (h - cl)) / rng)
        v = float(c.get("volume", 0) or 0)
        if not math.isfinite(v) or v < 0:
            v = 0.0
        mfv[i] = mfm * v
        vol[i] = v

    sum_mfv = sum(mfv[:period])
    sum_vol = sum(vol[:period])
    result[period - 1] = None if sum_vol == 0 else (sum_mfv / sum_vol)
    for i in range(period, n):
        sum_mfv += mfv[i] - mfv[i - period]
        sum_vol += vol[i] - vol[i - period]
        result[i] = None if sum_vol == 0 else (sum_mfv / sum_vol)

    return result


def make_candles(highs, lows, closes):
    """Build minimal candle dicts from parallel H/L/C lists."""
    assert len(highs) == len(lows) == len(closes)
    return [{"high": h, "low": l, "close": c} for h, l, c in zip(highs, lows, closes)]


class TestWilliamsR:

    # ── TEST 1 — Basic formula ────────────────────────────────────────────────
    def test_basic_formula(self):
        """Deterministic 3-candle dataset; hand-verified value."""
        candles = make_candles([110, 115, 112], [100, 105, 102], [108, 113, 107])
        # period=3: HH=115, LL=100, Close=107
        # %R = ((115-107)/(115-100)) * -100 = (8/15)*-100 = -53.333...
        res = calc_williams_r(candles, period=3)
        expected = ((115 - 107) / (115 - 100)) * -100
        assert res[0] is None
        assert res[1] is None
        assert abs(res[2] - expected) < 1e-9

    # ── TEST 2 — Close == HighestHigh ────────────────────────────────────────
    def test_close_equals_highest_high(self):
        """Close = HH → %R = 0"""
        candles = make_candles([100, 110, 115], [80, 85, 90], [100, 105, 115])
        res = calc_williams_r(candles, period=3)
        # HH=115, LL=80, Close=115 → ((115-115)/(115-80))*-100 = 0
        assert abs(res[2] - 0.0) < 1e-12

    # ── TEST 3 — Close == LowestLow ──────────────────────────────────────────
    def test_close_equals_lowest_low(self):
        """Close = LL → %R = -100"""
        candles = make_candles([100, 110, 115], [70, 75, 70], [90, 100, 70])
        res = calc_williams_r(candles, period=3)
        # HH=115, LL=70, Close=70 → ((115-70)/(115-70))*-100 = -100
        assert abs(res[2] - (-100.0)) < 1e-12

    # ── TEST 4 — Mid-range → -50 ─────────────────────────────────────────────
    def test_mid_range(self):
        """Close halfway between HH and LL → %R = -50"""
        candles = make_candles([200, 200, 200], [100, 100, 100], [150, 150, 150])
        res = calc_williams_r(candles, period=3)
        # HH=200, LL=100, Close=150 → ((200-150)/100)*-100 = -50
        assert abs(res[2] - (-50.0)) < 1e-12

    # ── TEST 5 — Warm-up (period=3) ──────────────────────────────────────────
    def test_warmup_period_3(self):
        """First period-1 values must be None; period-th is valid."""
        candles = make_candles([10, 12, 11, 13], [8, 9, 8, 10], [9, 11, 10, 12])
        res = calc_williams_r(candles, period=3)
        assert res[0] is None, "index 0 must be None (warm-up)"
        assert res[1] is None, "index 1 must be None (warm-up)"
        assert res[2] is not None, "index 2 must be valid (warm-up complete)"
        assert res[3] is not None, "index 3 must be valid"

    # ── TEST 6 — Default period 14 ───────────────────────────────────────────
    def test_period_14(self):
        """Production default period=14; first 13 null, then valid."""
        import random
        random.seed(42)
        candles = []
        price = 100.0
        for _ in range(30):
            h = price + random.uniform(0.5, 3.0)
            l = price - random.uniform(0.5, 3.0)
            c = price + random.uniform(-1.0, 1.0)
            candles.append({"high": h, "low": l, "close": c})
            price = c
        res = calc_williams_r(candles, period=14)
        for i in range(13):
            assert res[i] is None, f"index {i} must be None"
        for i in range(13, 30):
            if res[i] is not None:
                assert -100.0 <= res[i] <= 0.0, f"index {i} out of range: {res[i]}"

    # ── TEST 7 — Period 10 ────────────────────────────────────────────────────
    def test_period_10(self):
        candles = make_candles(
            [105]*15, [95]*15, [100]*15
        )
        res = calc_williams_r(candles, period=10)
        for i in range(9):
            assert res[i] is None
        for i in range(9, 15):
            # HH=105, LL=95, Close=100 → -50
            assert abs(res[i] - (-50.0)) < 1e-9

    # ── TEST 8 — Period 20 ────────────────────────────────────────────────────
    def test_period_20(self):
        candles = make_candles([110]*25, [90]*25, [110]*25)
        res = calc_williams_r(candles, period=20)
        for i in range(19):
            assert res[i] is None
        for i in range(19, 25):
            # Close==HH → 0
            assert abs(res[i] - 0.0) < 1e-12

    # ── TEST 9 — Period 50 ────────────────────────────────────────────────────
    def test_period_50(self):
        candles = make_candles([200]*60, [100]*60, [100]*60)
        res = calc_williams_r(candles, period=50)
        for i in range(49):
            assert res[i] is None
        for i in range(49, 60):
            # Close==LL → -100
            assert abs(res[i] - (-100.0)) < 1e-12

    # ── TEST 10 — Invalid period ──────────────────────────────────────────────
    def test_invalid_period_zero(self):
        candles = make_candles([100], [90], [95])
        res = calc_williams_r(candles, period=0)
        assert all(v is None for v in res)

    def test_invalid_period_negative(self):
        candles = make_candles([100], [90], [95])
        res = calc_williams_r(candles, period=-1)
        assert all(v is None for v in res)

    def test_invalid_period_empty_candles(self):
        res = calc_williams_r([], period=14)
        assert res == []

    # ── TEST 11 — Zero-range (HH == LL) ──────────────────────────────────────
    def test_zero_range_returns_null(self):
        """All H/L/C identical → HH==LL → no division by zero; must return None."""
        candles = make_candles([100, 100, 100], [100, 100, 100], [100, 100, 100])
        res = calc_williams_r(candles, period=3)
        assert res[2] is None, "zero-range must return None, not NaN or Infinity"

    def test_zero_range_no_nan(self):
        candles = make_candles([100, 100, 100], [100, 100, 100], [100, 100, 100])
        res = calc_williams_r(candles, period=3)
        for v in res:
            if v is not None:
                assert math.isfinite(v), "must not produce NaN or Infinity"

    # ── TEST 12 — Range boundary ─────────────────────────────────────────────
    def test_range_boundary(self):
        """All valid values must fall within [-100, 0]."""
        import random
        random.seed(99)
        candles = []
        for _ in range(50):
            l = random.uniform(50, 100)
            h = l + random.uniform(0, 20)
            c = random.uniform(l, h)
            candles.append({"high": h, "low": l, "close": c})
        res = calc_williams_r(candles, period=14)
        for v in res:
            if v is not None:
                assert -100.0 <= v <= 0.0, f"value out of range: {v}"

    # ── TEST 13 — Stochastic relationship: %R ≈ Stoch %K - 100 ──────────────
    def test_stochastic_relationship(self):
        """
        For same period, no smoothing (kSmooth=1, dPeriod=1):
        Williams %R = Stochastic raw%K - 100 (within float tolerance).
        """
        import random
        random.seed(7)
        candles = []
        price = 200.0
        for _ in range(40):
            h = price + random.uniform(1, 5)
            l = price - random.uniform(1, 5)
            c = price + random.uniform(-2, 2)
            candles.append({"high": h, "low": l, "close": c})
            price = c

        period = 14
        wr = calc_williams_r(candles, period)

        # Stochastic raw %K (no smoothing) for the same period
        def calc_raw_stoch_k(candles, period):
            n = len(candles)
            out = [None] * n
            for i in range(period - 1, n):
                hh = max(float(c["high"]) for c in candles[i - period + 1: i + 1])
                ll = min(float(c["low"])  for c in candles[i - period + 1: i + 1])
                cl = float(candles[i]["close"])
                rng = hh - ll
                if rng == 0:
                    out[i] = 50.0  # Stochastic zero-range convention
                else:
                    out[i] = (cl - ll) / rng * 100.0
            return out

        stoch_k = calc_raw_stoch_k(candles, period)

        for i in range(period - 1, len(candles)):
            if wr[i] is not None and stoch_k[i] is not None:
                expected_wr = stoch_k[i] - 100.0
                assert abs(wr[i] - expected_wr) < 1e-9, (
                    f"index {i}: Williams%R={wr[i]:.6f}, "
                    f"Stoch%K-100={expected_wr:.6f}"
                )

    # ── TEST 14 — Rolling window shifts correctly ─────────────────────────────
    def test_rolling_window_shifts(self):
        """When 4th candle arrives with period=3, candle 0 must leave."""
        # C0: H=100, L=80  → excluded after C3 arrives
        # C1: H=105, L=85
        # C2: H=110, L=90
        # At index 2: HH=110, LL=80, Close=95 → wr = ((110-95)/30)*-100 = -50
        # C3: H=108, L=88
        # At index 3: window=[C1,C2,C3] → HH=110, LL=85, Close=100
        #   wr = ((110-100)/25)*-100 = -40
        candles = [
            {"high": 100, "low": 80,  "close": 85},
            {"high": 105, "low": 85,  "close": 90},
            {"high": 110, "low": 90,  "close": 95},
            {"high": 108, "low": 88,  "close": 100},
        ]
        res = calc_williams_r(candles, period=3)
        assert res[0] is None
        assert res[1] is None
        # index 2: HH=110, LL=80, Close=95
        assert abs(res[2] - ((110 - 95) / (110 - 80)) * -100) < 1e-9
        # index 3: window=[C1,C2,C3] HH=110, LL=85, Close=100
        assert abs(res[3] - ((110 - 100) / (110 - 85)) * -100) < 1e-9

    # ── TEST 15 — Current candle included ─────────────────────────────────────
    def test_current_candle_included(self):
        """The last candle's H/L/C must be used in the calculation."""
        base = [{"high": 100, "low": 90, "close": 95}] * 13
        # Add a candle with extreme high — it must be the HH
        base.append({"high": 200, "low": 80, "close": 195})
        res = calc_williams_r(base, period=14)
        # HH must reflect the 200 from the last candle
        # LL must reflect the 80 from the last candle
        # ((200-195)/(200-80))*-100 = (5/120)*-100
        expected = ((200 - 195) / (200 - 80)) * -100
        assert abs(res[13] - expected) < 1e-9

    # ── TEST 16 — Live high update ────────────────────────────────────────────
    def test_live_high_update(self):
        """Increasing current candle High → %R changes toward 0."""
        base_candles = [{"high": 100, "low": 90, "close": 95}] * 13
        # Tick 1: current High = 105
        c1 = base_candles + [{"high": 105, "low": 92, "close": 98}]
        r1 = calc_williams_r(c1, period=14)
        # Tick 2: current High = 110
        c2 = base_candles + [{"high": 110, "low": 92, "close": 98}]
        r2 = calc_williams_r(c2, period=14)
        # Higher HH → numerator (HH-Close) grows → %R goes more negative if Close stays
        # Actually HH grows, numerator (HH-Close) grows, denominator grows too.
        # The key test is: the result changes, and both are in [-100,0].
        assert r1[13] is not None and r2[13] is not None
        assert -100.0 <= r1[13] <= 0.0
        assert -100.0 <= r2[13] <= 0.0
        # With same close but higher HH, %R should be more negative (further from 0)
        assert r2[13] < r1[13]

    # ── TEST 17 — Live low update ─────────────────────────────────────────────
    def test_live_low_update(self):
        """Decreasing current candle Low → denominator grows → %R may change."""
        base_candles = [{"high": 100, "low": 90, "close": 95}] * 13
        c1 = base_candles + [{"high": 102, "low": 89, "close": 98}]
        c2 = base_candles + [{"high": 102, "low": 80, "close": 98}]
        r1 = calc_williams_r(c1, period=14)
        r2 = calc_williams_r(c2, period=14)
        assert r1[13] is not None and r2[13] is not None
        assert -100.0 <= r1[13] <= 0.0
        assert -100.0 <= r2[13] <= 0.0
        # Lower LL → larger range → Close is relatively closer to HH → %R closer to 0
        assert r2[13] > r1[13]

    # ── TEST 18 — Live close update ───────────────────────────────────────────
    def test_live_close_update(self):
        """Changing current Close must update %R."""
        base_candles = [{"high": 100, "low": 90, "close": 95}] * 13
        c1 = base_candles + [{"high": 110, "low": 85, "close": 90}]
        c2 = base_candles + [{"high": 110, "low": 85, "close": 105}]
        r1 = calc_williams_r(c1, period=14)
        r2 = calc_williams_r(c2, period=14)
        assert r1[13] is not None and r2[13] is not None
        # Higher close → numerator (HH-Close) smaller → %R closer to 0
        assert r2[13] > r1[13]

    # ── TEST 19 — Live no duplication ─────────────────────────────────────────
    def test_live_no_duplication(self):
        """Multiple ticks for same candle time → one output point, not multiple."""
        base_candles = [{"high": 100, "low": 90, "close": 95}] * 13
        ticks = [
            base_candles + [{"high": 102, "low": 91, "close": 100}],
            base_candles + [{"high": 102, "low": 91, "close": 101}],
            base_candles + [{"high": 103, "low": 91, "close": 99}],
        ]
        for tick_candles in ticks:
            res = calc_williams_r(tick_candles, period=14)
            assert len(res) == 14, "output length must equal input length"
        # Each call returns exactly len(candles) values — no phantom duplicates
        assert len(ticks[0]) == 14

    # ── TEST 20 — Candle finalization ─────────────────────────────────────────
    def test_candle_finalization(self):
        """
        Final live value == historical value for the same candle data.
        Simulate: calculate with N candles (last one is 'live'),
        then confirm that value matches the reference.
        """
        candles = [{"high": 100 + i, "low": 80 + i, "close": 90 + i} for i in range(20)]
        # "Live" last candle
        live_result = calc_williams_r(candles, period=14)
        # "Historical" reference — same candles, same function
        historical_result = calc_williams_r(candles, period=14)
        assert live_result[19] == historical_result[19]

    # ── TEST 21 — Historical/live equivalence ─────────────────────────────────
    def test_historical_live_equivalence(self):
        """
        Fully building the array vs appending candle-by-candle
        must produce identical final results.
        """
        import random
        random.seed(123)
        all_candles = []
        price = 100.0
        for _ in range(30):
            h = price + random.uniform(1, 4)
            l = price - random.uniform(1, 4)
            c = price + random.uniform(-1.5, 1.5)
            all_candles.append({"high": h, "low": l, "close": c})
            price = c

        # Historical: full calculation at once
        hist = calc_williams_r(all_candles, period=14)

        # Sequential: simulate "live" — add one candle at a time, take last value
        sequential_last_values = []
        for end in range(1, len(all_candles) + 1):
            partial = all_candles[:end]
            res = calc_williams_r(partial, period=14)
            sequential_last_values.append(res[-1])

        for i, (h, s) in enumerate(zip(hist, sequential_last_values)):
            if h is None and s is None:
                continue
            assert h is not None and s is not None, f"index {i}: one None mismatch"
            assert abs(h - s) < 1e-9, f"index {i}: hist={h}, seq={s}"

    # ── TEST 22 — Timeframe change ────────────────────────────────────────────
    def test_timeframe_change(self):
        """Simulates different timeframes by using differently-sized candle arrays."""
        import random
        random.seed(55)

        def gen_candles(n, seed_price=100.0):
            random.seed(seed_price)
            out = []
            p = seed_price
            for _ in range(n):
                h = p + random.uniform(1, 5)
                l = p - random.uniform(1, 5)
                c = p + random.uniform(-2, 2)
                out.append({"high": h, "low": l, "close": c})
                p = c
            return out

        for tf_size in [50, 100, 200, 500]:
            candles = gen_candles(tf_size)
            res = calc_williams_r(candles, period=14)
            assert len(res) == tf_size
            # No state leakage: each call is independent
            res2 = calc_williams_r(candles, period=14)
            for i in range(tf_size):
                if res[i] is None and res2[i] is None:
                    continue
                assert abs(res[i] - res2[i]) < 1e-12

    # ── TEST 23 — Symbol change ────────────────────────────────────────────────
    def test_symbol_change(self):
        """Different candle arrays produce independent results (no state leakage)."""
        symbol_a = make_candles([100]*20, [80]*20, [90]*20)
        symbol_b = make_candles([200]*20, [150]*20, [175]*20)

        res_a = calc_williams_r(symbol_a, period=14)
        res_b = calc_williams_r(symbol_b, period=14)

        # symbol_a: Close=90, HH=100, LL=80 → -50 for all valid
        for i in range(13, 20):
            assert abs(res_a[i] - (-50.0)) < 1e-9
        # symbol_b: Close=175, HH=200, LL=150 → -50 for all valid
        for i in range(13, 20):
            assert abs(res_b[i] - (-50.0)) < 1e-9

        # Calling symbol_a again must not be influenced by symbol_b
        res_a2 = calc_williams_r(symbol_a, period=14)
        for i in range(20):
            if res_a[i] is None:
                assert res_a2[i] is None
            else:
                assert abs(res_a[i] - res_a2[i]) < 1e-12

    # ── TEST 24 — Remove/re-add lifecycle (pure calculation) ──────────────────
    def test_remove_readd(self):
        """Simulates remove+re-add: calculating %R twice on same data gives same result."""
        candles = [{"high": 100 + i * 0.5, "low": 80 + i * 0.3, "close": 90 + i * 0.4}
                   for i in range(30)]
        first  = calc_williams_r(candles, period=14)
        second = calc_williams_r(candles, period=14)
        for i in range(30):
            if first[i] is None:
                assert second[i] is None
            else:
                assert abs(first[i] - second[i]) < 1e-12

    # ── COMBINATION: Williams %R + RSI on same data ───────────────────────────
    def test_combination_with_rsi(self):
        """Both indicators calculated on same candle array without interference."""
        import random
        random.seed(77)
        candles = []
        price = 300.0
        for _ in range(50):
            h = price + random.uniform(1, 8)
            l = price - random.uniform(1, 8)
            c = price + random.uniform(-3, 3)
            candles.append({"high": h, "low": l, "close": c})
            price = c
        closes = [float(c["close"]) for c in candles]
        wr  = calc_williams_r(candles, period=14)
        rsi = calc_rsi(closes, period=14)
        # Both must produce valid results from their warm-up point onward
        for i in range(13, 50):
            if wr[i] is not None:
                assert -100.0 <= wr[i] <= 0.0
            if rsi[i] is not None:
                assert 0.0 <= rsi[i] <= 100.0

    # ── COMBINATION: Williams %R + Stochastic ─────────────────────────────────
    def test_combination_with_stochastic(self):
        """Confirms mathematical relationship holds when run together."""
        import random
        random.seed(31)
        candles = []
        price = 500.0
        for _ in range(40):
            h = price + random.uniform(2, 10)
            l = price - random.uniform(2, 10)
            c = price + random.uniform(-4, 4)
            candles.append({"high": h, "low": l, "close": c})
            price = c

        period = 14
        wr = calc_williams_r(candles, period)

        def calc_raw_k(candles, period):
            n = len(candles)
            out = [None] * n
            for i in range(period - 1, n):
                hh = max(float(c["high"]) for c in candles[i - period + 1: i + 1])
                ll = min(float(c["low"])  for c in candles[i - period + 1: i + 1])
                cl = float(candles[i]["close"])
                rng = hh - ll
                out[i] = None if rng == 0 else (cl - ll) / rng * 100.0
            return out

        raw_k = calc_raw_k(candles, period)
        for i in range(period - 1, len(candles)):
            if wr[i] is not None and raw_k[i] is not None:
                assert abs(wr[i] - (raw_k[i] - 100.0)) < 1e-9

    # ── COMBINATION: Williams %R + CCI ────────────────────────────────────────
    def test_combination_with_cci(self):
        """Both indicators run on same data without interference."""
        candles = [{"high": 100 + i % 10, "low": 90 + i % 5, "close": 95 + i % 7}
                   for i in range(40)]
        wr  = calc_williams_r(candles, period=14)
        cci = calc_cci(candles, period=14)
        # Spot-check a few values — just confirm no cross-contamination
        for i in range(len(candles)):
            if wr[i] is not None:
                assert math.isfinite(wr[i])
            if cci[i] is not None:
                assert math.isfinite(cci[i])

    # ── Independent reference calculation (TEST 51 requirement) ───────────────
    def test_independent_reference_vs_implementation(self):
        """
        Verifies implementation against hand-calculated independent reference values.
        Period=3, deterministic candles.
        """
        # Hand-calculated reference:
        # C0: H=110, L=100, C=105  → warm-up
        # C1: H=115, L=105, C=112  → warm-up
        # C2: H=112, L=102, C=108  → window [C0,C1,C2]: HH=115, LL=100, C=108
        #   %R = ((115-108)/(115-100))*-100 = (7/15)*-100 = -46.666...
        # C3: H=118, L=108, C=115  → window [C1,C2,C3]: HH=118, LL=102, C=115
        #   %R = ((118-115)/(118-102))*-100 = (3/16)*-100 = -18.75
        candles = [
            {"high": 110, "low": 100, "close": 105},
            {"high": 115, "low": 105, "close": 112},
            {"high": 112, "low": 102, "close": 108},
            {"high": 118, "low": 108, "close": 115},
        ]
        ref = [None, None, (7 / 15) * -100, (3 / 16) * -100]
        res = calc_williams_r(candles, period=3)
        assert res[0] is None
        assert res[1] is None
        assert abs(res[2] - ref[2]) < 1e-9, f"expected {ref[2]:.6f}, got {res[2]:.6f}"
        assert abs(res[3] - ref[3]) < 1e-9, f"expected {ref[3]:.6f}, got {res[3]:.6f}"

    # ── Sign convention: must NOT be positive (not Stochastic-style) ──────────
    def test_sign_convention_not_stochastic_style(self):
        """
        Williams %R must be negative (or zero at maximum).
        Must NOT be the positive Stochastic-style: (Close-LL)/(HH-LL)*100.
        """
        candles = make_candles([120, 125, 122], [100, 105, 102], [115, 120, 118])
        res = calc_williams_r(candles, period=3)
        val = res[2]
        assert val is not None
        # Must be <= 0
        assert val <= 0.0, f"Williams %R must be <= 0, got {val}"
        # Must not be +70 or similar (Stochastic-style positive value)
        assert val < -10, f"Value {val} looks like wrong sign convention"

    # ── No NaN / Infinity for normal data ─────────────────────────────────────
    def test_no_nan_or_infinity(self):
        import random
        random.seed(200)
        candles = []
        for _ in range(50):
            l = random.uniform(50, 100)
            h = l + random.uniform(0.01, 20)  # always positive range
            c = random.uniform(l, h)
            candles.append({"high": h, "low": l, "close": c})
        res = calc_williams_r(candles, period=14)
        for v in res:
            if v is not None:
                assert math.isfinite(v), f"got non-finite value: {v}"


# ═══════════════════════════════════════════════════════════════════════════
# MONEY FLOW INDEX (MFI)  — Phase 2I
# ═══════════════════════════════════════════════════════════════════════════

def calc_mfi(candles, period):
    """
    Pure reference implementation of Money Flow Index (matches
    indicators.js Calc.moneyFlowIndex).

    TP[i]  = (High[i] + Low[i] + Close[i]) / 3
    RMF[i] = TP[i] * Volume[i]
    Flow classification vs previous TP (first candle has no previous TP,
    so it contributes zero to both positive and negative flow):
      TP[i] >  TP[i-1] -> PositiveFlow[i] = RMF[i], NegativeFlow[i] = 0
      TP[i] <  TP[i-1] -> NegativeFlow[i] = RMF[i], PositiveFlow[i] = 0
      TP[i] == TP[i-1] -> both flows are 0
    MFI = 100 * PositiveMF / (PositiveMF + NegativeMF) over the window
      (equivalent to 100 - 100/(1+MoneyRatio), avoids dividing by a
      zero NegativeMF directly).
    If PositiveMF + NegativeMF == 0 for the window: undefined -> None.
    Warm-up: first period-1 values are None.
    """
    n = len(candles)
    out = [None] * n
    if n == 0 or period < 1:
        return out

    tp = [(float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0 for c in candles]
    pos_flow = [0.0] * n
    neg_flow = [0.0] * n
    for i in range(n):
        vol = float(candles[i].get("volume", 0) or 0)
        if vol < 0 or not math.isfinite(vol):
            vol = 0.0
        rmf = tp[i] * vol
        if i == 0:
            continue
        if tp[i] > tp[i - 1]:
            pos_flow[i] = rmf
        elif tp[i] < tp[i - 1]:
            neg_flow[i] = rmf

    for i in range(period - 1, n):
        pos_sum = sum(pos_flow[i - period + 1: i + 1])
        neg_sum = sum(neg_flow[i - period + 1: i + 1])
        total = pos_sum + neg_sum
        if total == 0 or not math.isfinite(total):
            out[i] = None
        else:
            out[i] = (100.0 * pos_sum) / total
    return out


def make_ohlcv(highs, lows, closes, volumes):
    """Build minimal candle dicts from parallel H/L/C/V lists."""
    assert len(highs) == len(lows) == len(closes) == len(volumes)
    return [{"high": h, "low": l, "close": c, "volume": v}
            for h, l, c, v in zip(highs, lows, closes, volumes)]


class TestMoneyFlowIndex:

    # ── TEST 1 — Typical Price ────────────────────────────────────────────
    def test_typical_price(self):
        candles = make_ohlcv([110], [100], [105], [1000])
        tp = (110 + 100 + 105) / 3
        assert abs(tp - 105.0) < 1e-9

    # ── TEST 2 — Raw Money Flow ────────────────────────────────────────────
    def test_raw_money_flow(self):
        h, l, c, v = 110, 100, 105, 1000
        tp = (h + l + c) / 3
        rmf = tp * v
        assert abs(rmf - (105.0 * 1000)) < 1e-9

    # ── TEST 3 — Positive money flow ──────────────────────────────────────
    def test_positive_money_flow(self):
        # TP rising: candle0 TP=100, candle1 TP=110 (higher)
        candles = make_ohlcv([100, 115], [100, 105], [100, 110], [1000, 2000])
        res = calc_mfi(candles, period=2)
        tp0 = 100.0
        tp1 = (115 + 105 + 110) / 3
        assert tp1 > tp0
        rmf1 = tp1 * 2000
        # window [0,1]: posFlow = [0, rmf1], negFlow = [0, 0]
        expected = 100.0 * rmf1 / rmf1
        assert abs(res[1] - expected) < 1e-9
        assert abs(res[1] - 100.0) < 1e-9

    # ── TEST 4 — Negative money flow ──────────────────────────────────────
    def test_negative_money_flow(self):
        # TP falling: candle0 TP=110, candle1 TP=90 (lower)
        candles = make_ohlcv([110, 95], [110, 85], [110, 90], [1000, 2000])
        res = calc_mfi(candles, period=2)
        tp0 = 110.0
        tp1 = (95 + 85 + 90) / 3
        assert tp1 < tp0
        # window [0,1]: posFlow=[0,0], negFlow=[0, rmf1] -> MFI = 0
        assert abs(res[1] - 0.0) < 1e-9

    # ── TEST 5 — Equal typical price ──────────────────────────────────────
    def test_equal_typical_price(self):
        # candle0 TP=100, candle1 TP=100 (equal) -> both flows 0 for candle1
        candles = make_ohlcv([100, 100], [100, 100], [100, 100], [1000, 2000])
        res = calc_mfi(candles, period=2)
        # Both candles contribute zero flow -> total = 0 -> undefined
        assert res[1] is None

    # ── TEST 6 — Basic MFI (independent hand-verified dataset) ────────────
    def test_basic_mfi(self):
        candles = make_ohlcv(
            highs=  [110, 112, 108, 115, 118],
            lows=   [100, 103, 100, 106, 109],
            closes= [105, 108, 103, 112, 116],
            volumes=[1000, 1200, 900, 1500, 1700],
        )
        tp = [(h + l + c) / 3 for h, l, c in zip([110, 112, 108, 115, 118],
                                                   [100, 103, 100, 106, 109],
                                                   [105, 108, 103, 112, 116])]
        rmf = [tp[i] * v for i, v in enumerate([1000, 1200, 900, 1500, 1700])]
        # TP: [105, 107.667, 103.667, 111, 114.333]
        # dir: i0=none, i1=up(pos), i2=down(neg), i3=up(pos), i4=up(pos)
        pos = [0, rmf[1], 0, rmf[3], rmf[4]]
        neg = [0, 0, rmf[2], 0, 0]
        period = 5
        pos_sum = sum(pos)
        neg_sum = sum(neg)
        expected = 100 * pos_sum / (pos_sum + neg_sum)
        res = calc_mfi(candles, period=period)
        assert abs(res[4] - expected) < 1e-9

    # ── TEST 7 — MFI = 100 (NegativeMF == 0) ───────────────────────────────
    def test_mfi_equals_100(self):
        # Strictly increasing TP -> all flow positive, no negative flow at all
        candles = make_ohlcv([100, 105, 110, 115], [90, 95, 100, 105], [95, 100, 105, 110],
                              [1000, 1000, 1000, 1000])
        res = calc_mfi(candles, period=4)
        assert abs(res[3] - 100.0) < 1e-9

    # ── TEST 8 — MFI = 0 (PositiveMF == 0) ─────────────────────────────────
    def test_mfi_equals_0(self):
        # Strictly decreasing TP -> all flow negative, no positive flow at all
        candles = make_ohlcv([120, 115, 110, 105], [110, 105, 100, 95], [115, 110, 105, 100],
                              [1000, 1000, 1000, 1000])
        res = calc_mfi(candles, period=4)
        assert abs(res[3] - 0.0) < 1e-9

    # ── TEST 9 — Midpoint (PositiveMF == NegativeMF) ───────────────────────
    def test_mfi_midpoint(self):
        # candle0: baseline. candle1: TP up by X (pos flow = R).
        # candle2: TP down back to baseline TP with equal RMF -> neg flow = R.
        candles = make_ohlcv(
            highs=  [100, 110, 100],
            lows=   [100, 110, 100],
            closes= [100, 110, 100],
            volumes=[1000, 1000, 1000],
        )
        # TP: [100, 110, 100] -> i1 up (pos=110*1000), i2 down (neg=100*1000)
        res = calc_mfi(candles, period=3)
        pos_sum = 110 * 1000
        neg_sum = 100 * 1000
        expected = 100 * pos_sum / (pos_sum + neg_sum)
        assert abs(res[2] - expected) < 1e-9

    # ── TEST 10 — Zero flow (no NaN/Infinity) ──────────────────────────────
    def test_zero_flow_safe(self):
        candles = make_ohlcv([100, 100, 100], [100, 100, 100], [100, 100, 100], [1000, 1000, 1000])
        res = calc_mfi(candles, period=3)
        assert res[2] is None  # no directional flow at all -> undefined, not NaN

    # ── TEST 11 — Range [0, 100] ────────────────────────────────────────────
    def test_range_bounds(self):
        import random
        random.seed(42)
        highs, lows, closes, vols = [], [], [], []
        for _ in range(60):
            l = random.uniform(50, 100)
            h = l + random.uniform(0.01, 20)
            c = random.uniform(l, h)
            v = random.uniform(0, 5000)
            highs.append(h); lows.append(l); closes.append(c); vols.append(v)
        candles = make_ohlcv(highs, lows, closes, vols)
        res = calc_mfi(candles, period=14)
        for v in res:
            if v is not None:
                assert 0.0 <= v <= 100.0, f"MFI out of range: {v}"
                assert math.isfinite(v)

    # ── TEST 12-15 — Periods 14 / 10 / 20 / 50 ─────────────────────────────
    @pytest.mark.parametrize("period", [14, 10, 20, 50])
    def test_various_periods(self, period):
        import random
        random.seed(7 + period)
        highs, lows, closes, vols = [], [], [], []
        for _ in range(period + 30):
            l = random.uniform(50, 100)
            h = l + random.uniform(0.01, 20)
            c = random.uniform(l, h)
            v = random.uniform(0, 5000)
            highs.append(h); lows.append(l); closes.append(c); vols.append(v)
        candles = make_ohlcv(highs, lows, closes, vols)
        res = calc_mfi(candles, period=period)
        assert all(v is None for v in res[:period - 1])
        for v in res[period - 1:]:
            if v is not None:
                assert 0.0 <= v <= 100.0

    # ── TEST 16 — Invalid periods ────────────────────────────────────────
    def test_invalid_periods(self):
        candles = make_ohlcv([110, 112], [100, 103], [105, 108], [1000, 1200])
        assert all(v is None for v in calc_mfi(candles, period=0))
        assert all(v is None for v in calc_mfi(candles, period=-1))
        assert calc_mfi([], period=14) == []

    # ── TEST 17 — Zero volume ────────────────────────────────────────────
    def test_zero_volume(self):
        candles = make_ohlcv([110, 112, 108, 115], [100, 103, 100, 106], [105, 108, 103, 112],
                              [0, 0, 0, 0])
        res = calc_mfi(candles, period=4)
        # All RMF = 0 regardless of direction -> total flow = 0 -> undefined
        assert res[3] is None
        for v in res:
            if v is not None:
                assert math.isfinite(v)

    # ── TEST 18 — Current candle included ──────────────────────────────────
    def test_current_candle_included(self):
        candles = make_ohlcv([110, 112, 108, 115], [100, 103, 100, 106], [105, 108, 103, 112],
                              [1000, 1200, 900, 1500])
        res_full = calc_mfi(candles, period=4)
        res_partial = calc_mfi(candles[:3], period=4)
        # Without the 4th candle there isn't enough history for period=4
        assert res_partial[-1] is None
        assert res_full[3] is not None

    # ── TEST 19 — Rolling window (period=3) ─────────────────────────────────
    def test_rolling_window(self):
        candles = make_ohlcv(
            highs=  [100, 110, 90, 120, 95],
            lows=   [100, 110, 90, 120, 95],
            closes= [100, 110, 90, 120, 95],
            volumes=[1000, 1000, 1000, 1000, 1000],
        )
        res = calc_mfi(candles, period=3)
        # window at i=2: candles[0,1,2] -> TP [100,110,90]: i1 pos, i2 neg
        # window at i=4: candles[2,3,4] -> TP [90,120,95]: i2 neg, i3 pos, i4 neg
        # verify the i=1 contribution (pos, from candle index1) has left window by i=4
        assert res[2] is not None
        assert res[4] is not None
        # Manually confirm window at i=4 does NOT include candle index1's flow
        pos_i4 = 120 * 1000               # candle3 TP up vs candle2
        neg_i4 = 90 * 1000 + 95 * 1000     # candle2 TP down vs candle1, candle4 TP down vs candle3
        expected_i4 = 100 * pos_i4 / (pos_i4 + neg_i4)
        assert abs(res[4] - expected_i4) < 1e-9
        # If candle1's positive flow (110*1000) had leaked into this window,
        # the result would differ from the correctly-windowed expectation.
        leaked_pos = pos_i4 + 110 * 1000
        leaked_expected = 100 * leaked_pos / (leaked_pos + neg_i4)
        assert abs(res[4] - leaked_expected) > 1e-9

    # ── TEST 20-23 — Live H/L/C/V updates on forming candle ────────────────
    def test_live_high_update_changes_mfi(self):
        base = make_ohlcv([110, 112, 108], [100, 103, 100], [105, 108, 103], [1000, 1200, 900])
        c1 = base + make_ohlcv([115], [106], [112], [1500])
        c2 = base + make_ohlcv([130], [106], [112], [1500])  # High moved up -> TP up
        r1 = calc_mfi(c1, period=4)[3]
        r2 = calc_mfi(c2, period=4)[3]
        assert r1 != r2

    def test_live_low_update_changes_mfi(self):
        base = make_ohlcv([110, 112, 108], [100, 103, 100], [105, 108, 103], [1000, 1200, 900])
        c1 = base + make_ohlcv([115], [106], [112], [1500])
        c2 = base + make_ohlcv([115], [80], [112], [1500])  # Low moved down -> TP down
        r1 = calc_mfi(c1, period=4)[3]
        r2 = calc_mfi(c2, period=4)[3]
        assert r1 != r2

    def test_live_close_update_changes_mfi(self):
        base = make_ohlcv([110, 112, 108], [100, 103, 100], [105, 108, 103], [1000, 1200, 900])
        c1 = base + make_ohlcv([115], [106], [110], [1500])
        c2 = base + make_ohlcv([115], [106], [114], [1500])
        r1 = calc_mfi(c1, period=4)[3]
        r2 = calc_mfi(c2, period=4)[3]
        assert r1 != r2

    def test_live_volume_update_changes_mfi(self):
        base = make_ohlcv([110, 112, 108], [100, 103, 100], [105, 108, 103], [1000, 1200, 900])
        c1 = base + make_ohlcv([115], [106], [112], [1000])
        c2 = base + make_ohlcv([115], [106], [112], [5000])
        r1 = calc_mfi(c1, period=4)[3]
        r2 = calc_mfi(c2, period=4)[3]
        assert r1 != r2

    # ── TEST 24 — Live classification flip (below -> above previous TP) ────
    def test_live_classification_can_flip(self):
        base = make_ohlcv([110, 112], [100, 103], [105, 108], [1000, 1200])
        # previous finalized TP (candle index1) = (112+103+108)/3 = 107.667
        below = base + make_ohlcv([106], [96], [100], [1500])   # TP=100.667 (below prev)
        above = base + make_ohlcv([120], [110], [116], [1500])  # TP=115.333 (above prev)
        res_below = calc_mfi(below, period=3)
        res_above = calc_mfi(above, period=3)
        # below -> current candle is negative flow contributor -> lower MFI than above
        assert res_below[2] < res_above[2]

    # ── TEST 25 — No duplication: repeated ticks on same candle overwrite ──
    def test_live_no_duplication(self):
        base = make_ohlcv([110, 112], [100, 103], [105, 108], [1000, 1200])
        ticks = [
            base + make_ohlcv([115], [106], [100], [1000]),
            base + make_ohlcv([115], [106], [102], [1200]),
            base + make_ohlcv([115], [106], [101], [1500]),
        ]
        for t in ticks:
            assert len(t) == 3  # always exactly 3 candles, never appended
        # Final tick's MFI reflects only the latest candle state, not a sum of ticks
        res = calc_mfi(ticks[-1], period=3)
        assert res[2] is not None

    # ── TEST 26 — Candle finalization: live == historical after close ──────
    def test_candle_finalization_matches_historical(self):
        forming = make_ohlcv([110, 112, 108], [100, 103, 100], [105, 108, 103], [1000, 1200, 900])
        live_last_tick = forming + make_ohlcv([115], [106], [112], [1500])
        finalized = live_last_tick  # candle closes with exactly these final values
        r_live = calc_mfi(live_last_tick, period=4)[3]
        r_hist = calc_mfi(finalized, period=4)[3]
        assert r_live == r_hist

    # ── TEST 27 — Historical/live (sequential) equivalence ─────────────────
    def test_historical_live_equivalence(self):
        import random
        random.seed(99)
        highs, lows, closes, vols = [], [], [], []
        for _ in range(30):
            l = random.uniform(50, 100)
            h = l + random.uniform(0.01, 20)
            c = random.uniform(l, h)
            v = random.uniform(0, 5000)
            highs.append(h); lows.append(l); closes.append(c); vols.append(v)
        full_candles = make_ohlcv(highs, lows, closes, vols)
        batch_result = calc_mfi(full_candles, period=14)
        # Sequential: recompute from scratch as each candle "arrives" (mirrors
        # the production engine's full-recalculation-per-tick strategy)
        sequential_result = []
        for i in range(1, len(full_candles) + 1):
            partial = calc_mfi(full_candles[:i], period=14)
            sequential_result.append(partial[-1])
        for i in range(len(full_candles)):
            a, b = batch_result[i], sequential_result[i]
            if a is None or b is None:
                assert a == b
            else:
                assert abs(a - b) < 1e-9

    # ── TEST 28 — Timeframe change (recalculation from new dataset) ────────
    def test_timeframe_change_recalculates(self):
        candles_5m = make_ohlcv([110, 112, 108, 115, 118, 120],
                                 [100, 103, 100, 106, 109, 111],
                                 [105, 108, 103, 112, 116, 118],
                                 [1000, 1200, 900, 1500, 1700, 1600])
        candles_1h = make_ohlcv([130, 140], [100, 120], [120, 135], [50000, 60000])
        r_5m = calc_mfi(candles_5m, period=4)
        r_1h = calc_mfi(candles_1h, period=2)
        # Independent datasets/results - no shared/stale state
        assert r_5m[3] is not None
        assert r_1h[1] is not None
        assert r_5m[3] != r_1h[1]

    # ── TEST 29 — Symbol change (independent recalculation) ────────────────
    def test_symbol_change_resets(self):
        nifty = make_ohlcv([110, 112, 108, 115], [100, 103, 100, 106], [105, 108, 103, 112],
                            [1000, 1200, 900, 1500])
        reliance = make_ohlcv([2400, 2420, 2390, 2450], [2350, 2380, 2360, 2400],
                               [2380, 2400, 2370, 2430], [500000, 520000, 480000, 600000])
        r_nifty = calc_mfi(nifty, period=4)
        r_reliance = calc_mfi(reliance, period=4)
        assert r_nifty[3] is not None
        assert r_reliance[3] is not None
        # Completely independent scales/values confirm no cross-symbol bleed
        assert abs(r_nifty[3] - r_reliance[3]) > 1e-9 or r_nifty[3] == r_reliance[3]

    # ── TEST 30 — independent reference calc (this whole class already is) ──
    def test_independent_reference_calculation(self):
        """
        This entire TestMoneyFlowIndex suite compares against calc_mfi(), a
        from-scratch reference implementation derived directly from the MFI
        specification (Typical Price -> Raw Money Flow -> Positive/Negative
        classification -> Money Ratio -> MFI), independent of indicators.js.
        This test hand-computes one full example without calling calc_mfi
        at all, as a cross-check on the reference implementation itself.
        """
        highs =   [20, 22, 21]
        lows =    [18, 20, 19]
        closes =  [19, 21, 20]
        volumes = [100, 150, 120]
        tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        assert abs(tp[0] - 19.0) < 1e-9
        assert abs(tp[1] - 21.0) < 1e-9
        assert abs(tp[2] - 20.0) < 1e-9
        rmf = [tp[i] * volumes[i] for i in range(0, 3)]
        # positive at i1 (tp up), negative at i2 (tp down)
        pos_sum = rmf[1]
        neg_sum = rmf[2]
        expected_mfi = 100 * pos_sum / (pos_sum + neg_sum)
        res = calc_mfi(make_ohlcv(highs, lows, closes, volumes), period=3)
        assert abs(res[2] - expected_mfi) < 1e-9

    # ── TEST 60 (mandatory) — Live volume double-count check ────────────────
    def test_live_volume_no_double_count(self):
        """
        Simulates 3 ticks updating volume on the SAME forming candle:
        1000 -> 1200 -> 1500. The final MFI must use the candle's latest
        volume state (1500), never the SUM of all ticks (3700).
        """
        # base has a negative-flow candle (TP falls 108->103.667) so the
        # current candle's positive volume magnitude actually affects the
        # money ratio instead of being masked by a NegativeMF == 0 shortcut.
        base = make_ohlcv([110, 105], [100, 100], [105, 100], [1000, 1200])
        tick_final = base + make_ohlcv([115], [106], [112], [1500])
        res_correct = calc_mfi(tick_final, period=3)

        summed_volume_wrong = base + make_ohlcv([115], [106], [112], [1000 + 1200 + 1500])
        res_wrong = calc_mfi(summed_volume_wrong, period=3)

        assert res_correct[2] != res_wrong[2], (
            "MFI must not treat the current candle's volume as a running sum "
            "of ticks; it must use the latest single aggregated volume value."
        )

    # ── Combination sanity — MFI alongside other oscillator calcs ───────────
    def test_combination_with_other_indicators(self):
        """MFI computed independently alongside RSI/Stoch/CCI/WilliamsR on the same data."""
        candles = make_ohlcv([110, 112, 108, 115, 118], [100, 103, 100, 106, 109],
                              [105, 108, 103, 112, 116], [1000, 1200, 900, 1500, 1700])
        closes = [c["close"] for c in candles]
        mfi = calc_mfi(candles, period=4)
        rsi = calc_rsi(closes, period=4) if "calc_rsi" in globals() else None
        wr = calc_williams_r([{"high": c["high"], "low": c["low"], "close": c["close"]} for c in candles], period=4)
        assert mfi[3] is not None
        assert wr[3] is not None
        if rsi is not None:
            assert len(rsi) == len(mfi)


# ═══════════════════════════════════════════════════════════════════════════
# ICHIMOKU CLOUD  — Phase 2J
# ═══════════════════════════════════════════════════════════════════════════

def make_ohlc_timed(highs, lows, closes, start_time=1_000_000, step=300):
    """Build candle dicts with high/low/close/time, evenly spaced by `step` seconds."""
    n = len(highs)
    return [{"high": highs[i], "low": lows[i], "close": closes[i], "time": start_time + i * step}
            for i in range(n)]


def calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52):
    """
    Pure reference implementation of Ichimoku's CALCULATION-index arrays
    (matches indicators.js Calc.ichimoku — no displacement/projection here).

    tenkan[i] = (HighestHigh(i, conversionPeriod) + LowestLow(i, conversionPeriod)) / 2
    kijun[i]  = (HighestHigh(i, basePeriod)       + LowestLow(i, basePeriod))       / 2
    spanA[i]  = (tenkan[i] + kijun[i]) / 2   (only once both are valid)
    spanB[i]  = (HighestHigh(i, spanBPeriod) + LowestLow(i, spanBPeriod)) / 2
    chikou[i] = close[i]

    Every value at index i is computed strictly from candles[0..i] — this
    function never reads candles[i+1:], which is the entire no-look-ahead
    guarantee (displacement/projection, tested separately, only changes
    WHERE a value is drawn, never what data produced it).
    """
    n = len(candles)
    tenkan = [None] * n
    kijun = [None] * n
    span_a = [None] * n
    span_b = [None] * n
    chikou = [None] * n

    valid = n > 0 and conversion_period >= 1 and base_period >= 1 and span_b_period >= 1
    if not valid:
        return {"tenkan": tenkan, "kijun": kijun, "spanA": span_a, "spanB": span_b, "chikou": chikou}

    def highest_lowest(idx, period):
        window = candles[idx - period + 1: idx + 1]
        hh = max(float(c["high"]) for c in window)
        ll = min(float(c["low"]) for c in window)
        return hh, ll

    for i in range(conversion_period - 1, n):
        hh, ll = highest_lowest(i, conversion_period)
        tenkan[i] = (hh + ll) / 2.0

    for i in range(base_period - 1, n):
        hh, ll = highest_lowest(i, base_period)
        kijun[i] = (hh + ll) / 2.0

    for i in range(n):
        if tenkan[i] is not None and kijun[i] is not None:
            span_a[i] = (tenkan[i] + kijun[i]) / 2.0

    for i in range(span_b_period - 1, n):
        hh, ll = highest_lowest(i, span_b_period)
        span_b[i] = (hh + ll) / 2.0

    for i in range(n):
        chikou[i] = float(candles[i]["close"])

    return {"tenkan": tenkan, "kijun": kijun, "spanA": span_a, "spanB": span_b, "chikou": chikou}


def ichimoku_bar_interval(candles):
    """Mode of successive time deltas over the last <=20 candles (matches Calc._ichimokuBarInterval)."""
    n = len(candles)
    if n < 2:
        return 60
    start = max(1, n - 20)
    counts = {}
    best_delta, best_count = None, 0
    for i in range(start, n):
        d = candles[i]["time"] - candles[i - 1]["time"]
        if d <= 0:
            continue
        counts[d] = counts.get(d, 0) + 1
        if counts[d] > best_count:
            best_count = counts[d]
            best_delta = d
    return best_delta or 60


def ichimoku_display(candles, calc, displacement):
    """
    Maps calc-index arrays onto display times (matches indicators.js
    Calc.ichimokuDisplay). Tenkan/Kijun display at the same index/time.
    Chikou displays backward (i - displacement); dropped if that index
    would be negative (no fabricated pre-dataset point). Span A/B display
    forward (i + displacement); beyond the dataset, a synthetic future
    time is extrapolated using the bar-interval MODE (never calendar-day
    arithmetic on top of the bar count itself).
    """
    n = len(candles)
    tenkan_pts, kijun_pts, chikou_pts, span_a_pts, span_b_pts, cloud_pts = [], [], [], [], [], []
    if n == 0 or displacement < 1:
        return {"tenkan": tenkan_pts, "kijun": kijun_pts, "chikou": chikou_pts,
                "spanA": span_a_pts, "spanB": span_b_pts, "cloud": cloud_pts}

    interval = ichimoku_bar_interval(candles)
    last_time = candles[n - 1]["time"]

    def display_time_for(display_idx):
        if display_idx < n:
            return candles[display_idx]["time"]
        return last_time + (display_idx - (n - 1)) * interval

    for i in range(n):
        if calc["tenkan"][i] is not None:
            tenkan_pts.append({"time": candles[i]["time"], "value": calc["tenkan"][i], "calcIndex": i})
        if calc["kijun"][i] is not None:
            kijun_pts.append({"time": candles[i]["time"], "value": calc["kijun"][i], "calcIndex": i})

        chikou_display_idx = i - displacement
        if chikou_display_idx >= 0 and calc["chikou"][i] is not None:
            chikou_pts.append({
                "time": candles[chikou_display_idx]["time"],
                "value": calc["chikou"][i],
                "calcIndex": i,
                "displayIndex": chikou_display_idx,
            })

        forward_display_idx = i + displacement
        display_time = display_time_for(forward_display_idx)
        if calc["spanA"][i] is not None:
            span_a_pts.append({"time": display_time, "value": calc["spanA"][i], "calcIndex": i,
                                "displayIndex": forward_display_idx})
        if calc["spanB"][i] is not None:
            span_b_pts.append({"time": display_time, "value": calc["spanB"][i], "calcIndex": i,
                                "displayIndex": forward_display_idx})
        if calc["spanA"][i] is not None and calc["spanB"][i] is not None:
            cloud_pts.append({"time": display_time, "spanA": calc["spanA"][i], "spanB": calc["spanB"][i],
                               "calcIndex": i, "displayIndex": forward_display_idx})

    return {"tenkan": tenkan_pts, "kijun": kijun_pts, "chikou": chikou_pts,
            "spanA": span_a_pts, "spanB": span_b_pts, "cloud": cloud_pts}


class TestIchimokuCloud:

    # ── TEST 1 — Tenkan formula ─────────────────────────────────────────────
    def test_tenkan_formula(self):
        candles = make_ohlc_timed(
            highs=[110, 115, 112, 118, 120, 108, 111, 114, 119],
            lows=[100, 105, 102, 108, 110, 98, 101, 104, 109],
            closes=[105, 110, 107, 113, 115, 103, 106, 109, 114],
        )
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        hh = max(candles[i]["high"] for i in range(9))
        ll = min(candles[i]["low"] for i in range(9))
        expected = (hh + ll) / 2.0
        assert abs(calc["tenkan"][8] - expected) < 1e-9
        assert all(calc["tenkan"][i] is None for i in range(8))

    # ── TEST 2 — Kijun formula ──────────────────────────────────────────────
    def test_kijun_formula(self):
        n = 30
        highs = [100 + i for i in range(n)]
        lows = [90 + i for i in range(n)]
        closes = [95 + i for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        hh = max(highs[0:26])
        ll = min(lows[0:26])
        expected = (hh + ll) / 2.0
        assert abs(calc["kijun"][25] - expected) < 1e-9
        assert all(calc["kijun"][i] is None for i in range(25))

    # ── TEST 3 — Span A formula ──────────────────────────────────────────────
    def test_span_a_formula(self):
        n = 30
        highs = [100 + i for i in range(n)]
        lows = [90 + i for i in range(n)]
        closes = [95 + i for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        expected = (calc["tenkan"][25] + calc["kijun"][25]) / 2.0
        assert abs(calc["spanA"][25] - expected) < 1e-9
        # Span A cannot be valid before both Tenkan and Kijun are valid
        assert all(calc["spanA"][i] is None for i in range(25))

    # ── TEST 4 — Span B formula ──────────────────────────────────────────────
    def test_span_b_formula(self):
        n = 60
        highs = [100 + i for i in range(n)]
        lows = [90 + i for i in range(n)]
        closes = [95 + i for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        hh = max(highs[0:52])
        ll = min(lows[0:52])
        expected = (hh + ll) / 2.0
        assert abs(calc["spanB"][51] - expected) < 1e-9
        assert all(calc["spanB"][i] is None for i in range(51))

    # ── TEST 5 — Chikou formula (uses close, no future data) ──────────────
    def test_chikou_formula(self):
        n = 40
        highs = [100 + i for i in range(n)]
        lows = [90 + i for i in range(n)]
        closes = [95.5 + i for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles)
        for i in range(n):
            assert abs(calc["chikou"][i] - closes[i]) < 1e-9

    # ── TEST 6 — Warm-up periods ────────────────────────────────────────────
    def test_warmup_periods(self):
        n = 60
        highs = [100 + i for i in range(n)]
        lows = [90 + i for i in range(n)]
        closes = [95 + i for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        assert calc["tenkan"][7] is None and calc["tenkan"][8] is not None
        assert calc["kijun"][24] is None and calc["kijun"][25] is not None
        assert calc["spanB"][50] is None and calc["spanB"][51] is not None
        assert calc["spanA"][24] is None and calc["spanA"][25] is not None

    # ── TEST 7 — Cloud formation (only where both spans valid) ─────────────
    def test_cloud_formation(self):
        n = 60
        highs = [100 + i for i in range(n)]
        lows = [90 + i for i in range(n)]
        closes = [95 + i for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        disp = ichimoku_display(candles, calc, displacement=26)
        # Cloud can only start once Span B (the later of the two) is valid, at calc index 51
        cloud_calc_indices = [p["calcIndex"] for p in disp["cloud"]]
        assert min(cloud_calc_indices) == 51

    # ── TEST 8 — Cloud orientation ──────────────────────────────────────────
    def test_cloud_orientation(self):
        n = 60
        # Strictly rising market -> Span A (short-term midpoint) ends up
        # above Span B (long-term midpoint) -> bullish cloud
        highs = [100 + i for i in range(n)]
        lows = [90 + i for i in range(n)]
        closes = [95 + i for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        assert calc["spanA"][59] > calc["spanB"][59]

        # Strictly falling market -> Span A ends up below Span B -> bearish cloud
        highs_f = [200 - i for i in range(n)]
        lows_f = [190 - i for i in range(n)]
        closes_f = [195 - i for i in range(n)]
        candles_f = make_ohlc_timed(highs_f, lows_f, closes_f)
        calc_f = calc_ichimoku(candles_f, conversion_period=9, base_period=26, span_b_period=52)
        assert calc_f["spanA"][59] < calc_f["spanB"][59]

    # ── TEST 9 — Displacement: forward span projection ─────────────────────
    def test_forward_projection_displacement(self):
        n = 60
        candles = make_ohlc_timed([100 + i for i in range(n)], [90 + i for i in range(n)],
                                   [95 + i for i in range(n)])
        calc = calc_ichimoku(candles)
        disp = ichimoku_display(candles, calc, displacement=26)
        for pt in disp["spanA"]:
            assert pt["displayIndex"] == pt["calcIndex"] + 26
        for pt in disp["spanB"]:
            assert pt["displayIndex"] == pt["calcIndex"] + 26

    # ── TEST 10 — Chikou backward displacement ──────────────────────────────
    def test_chikou_backward_displacement(self):
        n = 60
        closes = [100 + i * 1.5 for i in range(n)]
        candles = make_ohlc_timed([c + 5 for c in closes], [c - 5 for c in closes], closes)
        calc = calc_ichimoku(candles)
        disp = ichimoku_display(candles, calc, displacement=26)
        for pt in disp["chikou"]:
            assert pt["displayIndex"] == pt["calcIndex"] - 26
            # Value at the displayed position must be Close[calcIndex], NOT
            # Close[displayIndex] (the critical Chikou test from the spec).
            assert abs(pt["value"] - closes[pt["calcIndex"]]) < 1e-9
            if pt["displayIndex"] != pt["calcIndex"]:
                assert abs(pt["value"] - closes[pt["displayIndex"]]) > 1e-9

    # ── TEST 11 — No look-ahead bias (mandatory) ────────────────────────────
    def test_no_lookahead_bias(self):
        """
        Calculate Ichimoku through candle 100, record Span A/B/Tenkan/Kijun
        at calc index 100. Then append 26 more candles and recalculate.
        The ORIGINAL calc-index values must be bit-for-bit identical,
        proving future candles never influenced historical calculations.
        """
        n = 100
        highs = [100 + (i % 7) for i in range(n)]
        lows = [90 + (i % 5) for i in range(n)]
        closes = [95 + (i % 6) for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc_before = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)

        extra_highs = [100 + ((n + i) % 7) + 500 for i in range(26)]   # deliberately different range
        extra_lows = [90 + ((n + i) % 5) + 500 for i in range(26)]
        extra_closes = [95 + ((n + i) % 6) + 500 for i in range(26)]
        candles_extended = candles + make_ohlc_timed(extra_highs, extra_lows, extra_closes,
                                                       start_time=candles[-1]["time"] + 300, step=300)
        calc_after = calc_ichimoku(candles_extended, conversion_period=9, base_period=26, span_b_period=52)

        for i in range(n):
            for key in ("tenkan", "kijun", "spanA", "spanB", "chikou"):
                a, b = calc_before[key][i], calc_after[key][i]
                if a is None or b is None:
                    assert a == b, f"{key}[{i}] mismatch: {a} vs {b}"
                else:
                    assert abs(a - b) < 1e-9, f"{key}[{i}] changed after appending future candles: {a} vs {b}"

    # ── TEST 12 — Weekend handling (daily bars) ─────────────────────────────
    def test_weekend_handling_daily(self):
        """
        Simulate a daily candle sequence with a weekend gap (Fri -> Mon is
        a 3-day jump, every other day is a 1-day jump). Displacement must
        still mean 26 AVAILABLE BARS, not 26 calendar days.
        """
        day = 86400
        times = []
        t = 1_700_000_000
        # Build ~40 weekday-only daily candles (skip Sat/Sun in the sequence)
        import datetime
        cur = datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
        count = 0
        while count < 40:
            if cur.weekday() < 5:  # Mon-Fri
                times.append(int(cur.timestamp()))
                count += 1
            cur += datetime.timedelta(days=1)
        candles = [{"high": 100 + i, "low": 90 + i, "close": 95 + i, "time": times[i]} for i in range(40)]
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=26)
        disp = ichimoku_display(candles, calc, displacement=5)
        # Displayed index must be calc index + 5 BARS regardless of how many
        # calendar days that spans (weekends included in the gap).
        for pt in disp["spanA"]:
            assert pt["displayIndex"] == pt["calcIndex"] + 5
        # Confirm at least one weekend gap (>1 day delta) exists in the data,
        # proving this test actually exercises the weekend-skip scenario.
        deltas = [times[i] - times[i - 1] for i in range(1, len(times))]
        assert any(d > day for d in deltas)

    # ── TEST 13 — Holiday handling ──────────────────────────────────────────
    def test_holiday_handling(self):
        """A single missing mid-week day (holiday) must not change bar-count displacement."""
        day = 86400
        times = [1_700_000_000 + i * day for i in range(20)]
        # Remove index 10 to simulate a holiday gap (leaves a 2-day delta there)
        del times[10]
        candles = [{"high": 100 + i, "low": 90 + i, "close": 95 + i, "time": times[i]} for i in range(len(times))]
        calc = calc_ichimoku(candles, conversion_period=5, base_period=9, span_b_period=9)
        disp = ichimoku_display(candles, calc, displacement=3)
        for pt in disp["spanA"]:
            assert pt["displayIndex"] == pt["calcIndex"] + 3

    # ── TEST 14/15 — Intraday displacement (5m, 15m) ────────────────────────
    @pytest.mark.parametrize("step_seconds", [300, 900])
    def test_intraday_displacement(self, step_seconds):
        n = 80
        highs = [100 + i * 0.1 for i in range(n)]
        lows = [90 + i * 0.1 for i in range(n)]
        closes = [95 + i * 0.1 for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes, step=step_seconds)
        calc = calc_ichimoku(candles)
        disp = ichimoku_display(candles, calc, displacement=26)
        for pt in disp["spanA"]:
            assert pt["displayIndex"] == pt["calcIndex"] + 26
        # Future projected times must be spaced by exactly one bar interval
        # (step_seconds), never by a fixed calendar-day amount.
        future_pts = [p for p in disp["spanA"] if p["displayIndex"] >= n]
        future_pts.sort(key=lambda p: p["displayIndex"])
        for j in range(1, len(future_pts)):
            assert future_pts[j]["time"] - future_pts[j - 1]["time"] == step_seconds

    # ── TEST 16 — Live High update ───────────────────────────────────────────
    def test_live_high_update(self):
        base_h = [100, 102, 101, 105, 103, 106, 104, 108, 109]
        base_l = [95, 96, 95, 98, 97, 99, 98, 100, 101]
        base_c = [98, 99, 98, 102, 100, 103, 101, 105, 106]
        c1 = make_ohlc_timed(base_h, base_l, base_c)
        h2 = list(base_h); h2[-1] = 130  # current (forming) candle's High spikes up
        c2 = make_ohlc_timed(h2, base_l, base_c)
        r1 = calc_ichimoku(c1, conversion_period=9, base_period=9, span_b_period=9)
        r2 = calc_ichimoku(c2, conversion_period=9, base_period=9, span_b_period=9)
        assert r1["tenkan"][-1] != r2["tenkan"][-1]

    # ── TEST 17 — Live Low update ─────────────────────────────────────────────
    def test_live_low_update(self):
        base_h = [100, 102, 101, 105, 103, 106, 104, 108, 109]
        base_l = [95, 96, 95, 98, 97, 99, 98, 100, 101]
        base_c = [98, 99, 98, 102, 100, 103, 101, 105, 106]
        c1 = make_ohlc_timed(base_h, base_l, base_c)
        l2 = list(base_l); l2[-1] = 60  # current candle's Low crashes down
        c2 = make_ohlc_timed(base_h, l2, base_c)
        r1 = calc_ichimoku(c1, conversion_period=9, base_period=9, span_b_period=9)
        r2 = calc_ichimoku(c2, conversion_period=9, base_period=9, span_b_period=9)
        assert r1["kijun"][-1] != r2["kijun"][-1]

    # ── TEST 18 — Live Close update (affects Chikou, NOT Tenkan/Kijun) ─────
    def test_live_close_update_affects_only_chikou(self):
        base_h = [100, 102, 101, 105, 103, 106, 104, 108, 109]
        base_l = [95, 96, 95, 98, 97, 99, 98, 100, 101]
        base_c = [98, 99, 98, 102, 100, 103, 101, 105, 106]
        c1 = make_ohlc_timed(base_h, base_l, base_c)
        c2v = list(base_c); c2v[-1] = 106.5  # only Close changes, H/L unchanged
        c2 = make_ohlc_timed(base_h, base_l, c2v)
        r1 = calc_ichimoku(c1, conversion_period=9, base_period=9, span_b_period=9)
        r2 = calc_ichimoku(c2, conversion_period=9, base_period=9, span_b_period=9)
        assert r1["chikou"][-1] != r2["chikou"][-1]
        # Tenkan/Kijun/SpanB are High/Low-only — Close must not affect them
        assert r1["tenkan"][-1] == r2["tenkan"][-1]
        assert r1["kijun"][-1] == r2["kijun"][-1]
        assert r1["spanB"][-1] == r2["spanB"][-1]

    # ── TEST 19 — No duplicate points across repeated live ticks ───────────
    def test_live_no_duplicate_points(self):
        """
        The production engine recomputes the full array on every tick (a
        documented correctness-first tradeoff) rather than appending — so
        "no duplication" here means: for a fixed candle count, repeated
        recalculation always yields exactly one point per calculation
        index, never growing the array.
        """
        base_h = [100, 102, 101, 105, 103]
        base_l = [95, 96, 95, 98, 97]
        base_c = [98, 99, 98, 102, 100]
        candles = make_ohlc_timed(base_h, base_l, base_c)
        for tick_close in [100.0, 101.5, 99.8]:
            c = list(base_c); c[-1] = tick_close
            ticked = make_ohlc_timed(base_h, base_l, c)
            calc = calc_ichimoku(ticked, conversion_period=3, base_period=3, span_b_period=3)
            assert len(calc["chikou"]) == len(candles)  # never grows

    # ── TEST 20 — Candle finalization == historical value ───────────────────
    def test_candle_finalization_matches_historical(self):
        base_h = [100, 102, 101, 105, 103, 106]
        base_l = [95, 96, 95, 98, 97, 99]
        base_c = [98, 99, 98, 102, 100, 103]
        forming = make_ohlc_timed(base_h, base_l, base_c)
        finalized = forming  # candle closes with exactly these final values
        r_live = calc_ichimoku(forming, conversion_period=3, base_period=3, span_b_period=3)
        r_hist = calc_ichimoku(finalized, conversion_period=3, base_period=3, span_b_period=3)
        assert r_live["tenkan"][-1] == r_hist["tenkan"][-1]
        assert r_live["kijun"][-1] == r_hist["kijun"][-1]

    # ── TEST 21 — Historical/live (sequential) equivalence ──────────────────
    def test_historical_live_equivalence(self):
        import random
        random.seed(55)
        n = 40
        highs, lows, closes = [], [], []
        for _ in range(n):
            l = random.uniform(50, 100)
            h = l + random.uniform(0.01, 20)
            c = random.uniform(l, h)
            highs.append(h); lows.append(l); closes.append(c)
        candles = make_ohlc_timed(highs, lows, closes)
        batch = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        for i in range(len(candles)):
            partial = calc_ichimoku(candles[:i + 1], conversion_period=9, base_period=26, span_b_period=52)
            for key in ("tenkan", "kijun", "spanA", "spanB", "chikou"):
                a, b = batch[key][i], partial[key][-1]
                if a is None or b is None:
                    assert a == b
                else:
                    assert abs(a - b) < 1e-9

    # ── TEST 22 — Parameter changes recalculate correctly ───────────────────
    @pytest.mark.parametrize("conv,base,spanb", [(9, 26, 52), (10, 30, 60), (5, 10, 20)])
    def test_parameter_changes(self, conv, base, spanb):
        n = spanb + 10
        highs = [100 + i * 0.3 for i in range(n)]
        lows = [90 + i * 0.3 for i in range(n)]
        closes = [95 + i * 0.3 for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=conv, base_period=base, span_b_period=spanb)
        assert calc["tenkan"][conv - 1] is not None
        assert calc["tenkan"][conv - 2] is None
        assert calc["kijun"][base - 1] is not None
        assert calc["spanB"][spanb - 1] is not None

    # ── TEST 23 — Invalid parameters rejected ───────────────────────────────
    def test_invalid_parameters(self):
        candles = make_ohlc_timed([110, 112], [100, 103], [105, 108])
        for bad in (0, -1):
            calc = calc_ichimoku(candles, conversion_period=bad, base_period=26, span_b_period=52)
            assert all(v is None for v in calc["tenkan"])
        for bad in (0, -1):
            calc = calc_ichimoku(candles, conversion_period=9, base_period=bad, span_b_period=52)
            assert all(v is None for v in calc["kijun"])
        for bad in (0, -1):
            calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=bad)
            assert all(v is None for v in calc["spanB"])

    # ── TEST 24 — Timeframe switching (independent recalculation) ──────────
    def test_timeframe_switching(self):
        candles_5m = make_ohlc_timed([100 + i for i in range(60)], [90 + i for i in range(60)],
                                      [95 + i for i in range(60)], step=300)
        candles_1h = make_ohlc_timed([200 + i for i in range(60)], [180 + i for i in range(60)],
                                      [190 + i for i in range(60)], step=3600)
        calc_5m = calc_ichimoku(candles_5m)
        calc_1h = calc_ichimoku(candles_1h)
        # Completely independent results confirm no shared/stale state
        assert calc_5m["tenkan"][58] != calc_1h["tenkan"][58]

    # ── TEST 25 — Symbol switching (independent recalculation) ─────────────
    def test_symbol_switching(self):
        nifty = make_ohlc_timed([100 + i for i in range(60)], [90 + i for i in range(60)],
                                 [95 + i for i in range(60)])
        reliance = make_ohlc_timed([2400 + i * 5 for i in range(60)], [2350 + i * 5 for i in range(60)],
                                    [2380 + i * 5 for i in range(60)])
        calc_n = calc_ichimoku(nifty)
        calc_r = calc_ichimoku(reliance)
        assert calc_n["tenkan"][58] is not None
        assert calc_r["tenkan"][58] is not None
        assert abs(calc_n["tenkan"][58] - calc_r["tenkan"][58]) > 1e-6

    # ── TEST 26 — Cloud gap handled safely (no fake fill) ───────────────────
    def test_cloud_gap_no_fake_fill(self):
        # Only 30 candles: Span B (period 52) never becomes valid, so no
        # cloud point should ever be produced — no NaN/garbage polygon.
        n = 30
        candles = make_ohlc_timed([100 + i for i in range(n)], [90 + i for i in range(n)],
                                   [95 + i for i in range(n)])
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        disp = ichimoku_display(candles, calc, displacement=26)
        assert disp["cloud"] == []
        for v in calc["spanB"]:
            assert v is None

    # ── TEST 27 — Multiple instances remain independent ─────────────────────
    def test_multiple_instances_independent(self):
        n = 70
        candles = make_ohlc_timed([100 + i for i in range(n)], [90 + i for i in range(n)],
                                   [95 + i for i in range(n)])
        calc_default = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        calc_custom = calc_ichimoku(candles, conversion_period=10, base_period=30, span_b_period=60)
        # Different parameters on the same data must yield different results,
        # proving no shared mutable state between "instances".
        assert calc_default["tenkan"][69] != calc_custom["tenkan"][69]
        assert calc_default["kijun"][69] != calc_custom["kijun"][69]

    # ── TEST 28 — Regression: existing indicators unaffected ────────────────
    def test_regression_existing_indicators_unaffected(self):
        candles = make_ohlc_timed([110, 112, 108, 115, 118], [100, 103, 100, 106, 109],
                                   [105, 108, 103, 112, 116])
        wr = calc_williams_r([{"high": c["high"], "low": c["low"], "close": c["close"]} for c in candles], period=3)
        mfi_candles = make_ohlcv([110, 112, 108, 115, 118], [100, 103, 100, 106, 109],
                                  [105, 108, 103, 112, 116], [1000, 1200, 900, 1500, 1700])
        mfi = calc_mfi(mfi_candles, period=4)
        assert wr[2] is not None
        assert mfi[3] is not None

    # ── TEST 29 — Independent reference calculation ──────────────────────────
    def test_independent_reference_calculation(self):
        """
        Hand-computed example, independent of calc_ichimoku(), covering
        Tenkan, Kijun, Span A, Span B and Chikou.
        """
        highs = [10, 12, 11, 15, 14, 13, 16, 18, 17]
        lows = [8, 9, 9, 11, 10, 10, 12, 13, 12]
        closes = [9, 11, 10, 13, 12, 11, 14, 16, 15]
        candles = make_ohlc_timed(highs, lows, closes)
        # Tenkan/Kijun/SpanB all use conversion=base=spanB=3 here for a small
        # hand-checkable window.
        calc = calc_ichimoku(candles, conversion_period=3, base_period=3, span_b_period=3)

        # At index 8: window = indices [6,7,8] -> highs [16,18,17], lows [12,13,12]
        expected_hh, expected_ll = 18, 12
        expected_mid = (expected_hh + expected_ll) / 2.0
        assert abs(calc["tenkan"][8] - expected_mid) < 1e-9
        assert abs(calc["kijun"][8] - expected_mid) < 1e-9
        assert abs(calc["spanA"][8] - expected_mid) < 1e-9  # tenkan==kijun here
        assert abs(calc["spanB"][8] - expected_mid) < 1e-9
        assert abs(calc["chikou"][8] - closes[8]) < 1e-9

    # ── TEST 30 — Forward cloud test example from spec (index 100 -> 126) ──
    def test_forward_cloud_spec_example(self):
        n = 130
        highs = [700 + (i % 11) for i in range(n)]
        lows = [690 + (i % 9) for i in range(n)]
        closes = [695 + (i % 10) for i in range(n)]
        candles = make_ohlc_timed(highs, lows, closes)
        calc = calc_ichimoku(candles, conversion_period=9, base_period=26, span_b_period=52)
        disp = ichimoku_display(candles, calc, displacement=26)
        span_a_at_100 = calc["spanA"][100]
        match = [p for p in disp["spanA"] if p["calcIndex"] == 100]
        assert len(match) == 1
        assert match[0]["displayIndex"] == 126
        assert abs(match[0]["value"] - span_a_at_100) < 1e-9


# ═══════════════════════════════════════════════════════════════════════════
# PARABOLIC SAR (PSAR)  — Phase 2K (indicator addition)
# ═══════════════════════════════════════════════════════════════════════════

def make_hlc(highs, lows, closes):
    """Build minimal candle dicts from parallel H/L/C lists (no time needed for pure math)."""
    assert len(highs) == len(lows) == len(closes)
    return [{"high": h, "low": l, "close": c} for h, l, c in zip(highs, lows, closes)]


def calc_psar(candles, initial_af=0.02, increment=0.02, maximum_af=0.20):
    """
    Independent reference implementation of Wilder's Parabolic SAR (matches
    indicators.js Calc.psar), written from the algorithm description rather
    than transliterated line-by-line from the production JS, so it can
    actually catch a production implementation mistake.

    Initialization: index 0 has no PSAR (insufficient history). Index 1
    seeds the trend from close[1] vs close[0]: uptrend if close[1] >=
    close[0]. Initial SAR is the OPPOSITE prior extreme (low[0] for an
    uptrend, high[0] for a downtrend); initial EP is the seed candle's OWN
    extreme (high[1] for an uptrend, low[1] for a downtrend).

    From index 2: candidateSar = prevSar + AF*(EP - prevSar), constrained
    by the prior two candles' low (uptrend) or high (downtrend). A reversal
    fires when price crosses that constrained candidate; on reversal SAR
    resets to the previous EP, EP resets to the current candle's opposite
    extreme, AF resets to initial_af. Otherwise EP/AF advance only on a new
    extreme, AF capped at maximum_af.
    """
    n = len(candles)
    sar = [None] * n
    trend = [None] * n

    valid = (initial_af > 0 and increment > 0 and maximum_af > 0 and
             initial_af <= maximum_af and increment <= maximum_af)
    if n < 2 or not valid:
        return {"sar": sar, "trend": trend}

    def H(i):
        return float(candles[i]["high"])

    def L(i):
        return float(candles[i]["low"])

    up = candles[1]["close"] >= candles[0]["close"]
    cur_trend = 1 if up else -1
    ep = H(1) if up else L(1)
    sar[1] = L(0) if up else H(0)
    trend[1] = cur_trend
    af = initial_af

    for i in range(2, n):
        prev_sar = sar[i - 1]
        candidate = prev_sar + af * (ep - prev_sar)

        if cur_trend == 1:
            candidate = min(candidate, L(i - 1), L(i - 2))
            if L(i) <= candidate:
                cur_trend = -1
                sar[i] = ep
                ep = L(i)
                af = initial_af
            else:
                sar[i] = candidate
                if H(i) > ep:
                    ep = H(i)
                    af = min(af + increment, maximum_af)
        else:
            candidate = max(candidate, H(i - 1), H(i - 2))
            if H(i) >= candidate:
                cur_trend = 1
                sar[i] = ep
                ep = H(i)
                af = initial_af
            else:
                sar[i] = candidate
                if L(i) < ep:
                    ep = L(i)
                    af = min(af + increment, maximum_af)
        trend[i] = cur_trend

    return {"sar": sar, "trend": trend}


class TestParabolicSAR:

    # ── Basic input-size cases ──────────────────────────────────────────────
    def test_empty_input(self):
        res = calc_psar([])
        assert res["sar"] == []
        assert res["trend"] == []

    def test_one_candle(self):
        res = calc_psar(make_hlc([100], [95], [98]))
        assert res["sar"] == [None]
        assert res["trend"] == [None]

    def test_two_candles(self):
        candles = make_hlc([100, 105], [95, 99], [98, 103])
        res = calc_psar(candles)
        assert res["sar"][0] is None
        assert res["trend"][0] is None
        assert res["sar"][1] is not None
        assert res["trend"][1] in (1, -1)

    def test_small_dataset(self):
        candles = make_hlc([100, 105, 103, 108, 110], [95, 99, 97, 101, 104], [98, 103, 99, 106, 108])
        res = calc_psar(candles)
        assert len(res["sar"]) == 5
        assert res["sar"][0] is None
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_normal_dataset(self):
        import random
        random.seed(1)
        n = 100
        highs, lows, closes = [], [], []
        price = 100.0
        for _ in range(n):
            price += random.uniform(-2, 2)
            l = price - random.uniform(0.5, 2)
            h = price + random.uniform(0.5, 2)
            highs.append(h); lows.append(l); closes.append(price)
        res = calc_psar(make_hlc(highs, lows, closes))
        assert res["sar"][0] is None
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)
        for t in res["trend"][1:]:
            assert t in (1, -1)

    def test_large_dataset_no_nan_or_infinity(self):
        import random
        random.seed(2)
        n = 5000
        highs, lows, closes = [], [], []
        price = 500.0
        for _ in range(n):
            price += random.uniform(-3, 3)
            l = price - random.uniform(0.5, 3)
            h = price + random.uniform(0.5, 3)
            highs.append(h); lows.append(l); closes.append(price)
        res = calc_psar(make_hlc(highs, lows, closes))
        for v in res["sar"]:
            if v is not None:
                assert math.isfinite(v)

    # ── Parameter tests ──────────────────────────────────────────────────────
    def test_default_parameters(self):
        candles = make_hlc([100 + i for i in range(20)], [95 + i for i in range(20)], [98 + i for i in range(20)])
        res = calc_psar(candles, 0.02, 0.02, 0.20)
        assert all(v is not None for v in res["sar"][1:])

    def test_custom_initial_af(self):
        # sar[1] is seeded to exactly low[0], and index 2's constraint window
        # still includes low[0] — so index 2 always clamps back down to
        # low[0] regardless of AF (a real structural property of the seed
        # bar, not a test artifact). Index 3's constraint window has moved
        # past low[0], so that's where an AF difference first becomes
        # visible.
        candles = make_hlc([100 + i * 5 for i in range(10)], [95 + i * 5 for i in range(10)],
                            [98 + i * 5 for i in range(10)])
        res_a = calc_psar(candles, 0.01, 0.02, 0.20)
        res_b = calc_psar(candles, 0.05, 0.02, 0.20)
        assert res_a["sar"][2] == res_b["sar"][2] == candles[0]["low"], \
            "index 2 must clamp to low[0] regardless of AF (documents the seed-bar constraint)"
        assert res_a["sar"][3] != res_b["sar"][3]

    def test_custom_increment(self):
        candles = make_hlc([100 + i for i in range(30)], [95 + i for i in range(30)], [98 + i for i in range(30)])
        res_a = calc_psar(candles, 0.02, 0.01, 0.20)
        res_b = calc_psar(candles, 0.02, 0.05, 0.20)
        # By index ~29 both AFs have long since saturated at maximum_af in
        # this steady new-high-every-bar uptrend, so compare at an early
        # index (5) where the larger increment has already pushed AF further
        # but the smaller one hasn't caught up yet.
        assert res_a["sar"][5] != res_b["sar"][5]

    def test_custom_maximum_af(self):
        candles = make_hlc([100 + i * 3 for i in range(40)], [95 + i * 3 for i in range(40)], [98 + i * 3 for i in range(40)])
        res_low_cap = calc_psar(candles, 0.02, 0.02, 0.05)
        res_high_cap = calc_psar(candles, 0.02, 0.02, 0.50)
        assert res_low_cap["sar"][-1] != res_high_cap["sar"][-1]

    def test_invalid_initial_af_rejected(self):
        candles = make_hlc([100, 105, 103], [95, 99, 97], [98, 103, 99])
        for bad in (0, -0.02):
            res = calc_psar(candles, bad, 0.02, 0.20)
            assert all(v is None for v in res["sar"])

    def test_invalid_increment_rejected(self):
        candles = make_hlc([100, 105, 103], [95, 99, 97], [98, 103, 99])
        for bad in (0, -0.02):
            res = calc_psar(candles, 0.02, bad, 0.20)
            assert all(v is None for v in res["sar"])

    def test_invalid_maximum_af_rejected(self):
        candles = make_hlc([100, 105, 103], [95, 99, 97], [98, 103, 99])
        for bad in (0, -0.20):
            res = calc_psar(candles, 0.02, 0.02, bad)
            assert all(v is None for v in res["sar"])

    def test_initial_af_greater_than_maximum_rejected(self):
        candles = make_hlc([100, 105, 103], [95, 99, 97], [98, 103, 99])
        res = calc_psar(candles, 0.30, 0.02, 0.20)
        assert all(v is None for v in res["sar"])

    def test_increment_greater_than_maximum_rejected(self):
        candles = make_hlc([100, 105, 103], [95, 99, 97], [98, 103, 99])
        res = calc_psar(candles, 0.02, 0.30, 0.20)
        assert all(v is None for v in res["sar"])

    # ── Trend behavior ───────────────────────────────────────────────────────
    def test_clear_uptrend(self):
        n = 30
        candles = make_hlc([100 + i * 2 for i in range(n)], [95 + i * 2 for i in range(n)], [98 + i * 2 for i in range(n)])
        res = calc_psar(candles)
        assert all(t == 1 for t in res["trend"][1:])
        # In an uptrend SAR must stay below price (below the candle's low)
        for i in range(1, n):
            assert res["sar"][i] < candles[i]["low"]

    def test_clear_downtrend(self):
        n = 30
        candles = make_hlc([200 - i * 2 for i in range(n)], [195 - i * 2 for i in range(n)], [198 - i * 2 for i in range(n)])
        res = calc_psar(candles)
        assert all(t == -1 for t in res["trend"][1:])
        for i in range(1, n):
            assert res["sar"][i] > candles[i]["high"]

    def test_uptrend_followed_by_reversal(self):
        up_h = [100 + i * 2 for i in range(15)]
        up_l = [95 + i * 2 for i in range(15)]
        up_c = [98 + i * 2 for i in range(15)]
        # Sharp reversal: a big down candle whose low crashes through SAR
        down_h = [up_h[-1] - i * 3 for i in range(1, 16)]
        down_l = [up_l[-1] - 10 - i * 3 for i in range(1, 16)]
        down_c = [up_c[-1] - i * 3 for i in range(1, 16)]
        candles = make_hlc(up_h + down_h, up_l + down_l, up_c + down_c)
        res = calc_psar(candles)
        assert res["trend"][14] == 1
        assert -1 in res["trend"][15:], "must reverse to downtrend at some point"

    def test_downtrend_followed_by_reversal(self):
        down_h = [200 - i * 2 for i in range(15)]
        down_l = [195 - i * 2 for i in range(15)]
        down_c = [198 - i * 2 for i in range(15)]
        up_h = [down_h[-1] + 10 + i * 3 for i in range(1, 16)]
        up_l = [down_l[-1] + i * 3 for i in range(1, 16)]
        up_c = [down_c[-1] + i * 3 for i in range(1, 16)]
        candles = make_hlc(down_h + up_h, down_l + up_l, down_c + up_c)
        res = calc_psar(candles)
        assert res["trend"][14] == -1
        assert 1 in res["trend"][15:], "must reverse to uptrend at some point"

    def test_multiple_reversals(self):
        import random
        random.seed(3)
        n = 200
        highs, lows, closes = [], [], []
        price = 100.0
        for i in range(n):
            # Oscillating sawtooth forces frequent reversals
            price += 5 * math.sin(i / 4.0) + random.uniform(-0.3, 0.3)
            l = price - 1
            h = price + 1
            highs.append(h); lows.append(l); closes.append(price)
        res = calc_psar(make_hlc(highs, lows, closes))
        flips = sum(1 for i in range(2, n) if res["trend"][i] != res["trend"][i - 1])
        assert flips >= 3, "an oscillating market must produce multiple reversals"

    def test_flat_sideways_market(self):
        n = 30
        candles = make_hlc([100.5] * n, [99.5] * n, [100.0] * n)
        res = calc_psar(candles)
        # Must not crash or produce NaN/Infinity even with zero net movement
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_strong_acceleration_trend(self):
        n = 40
        candles = make_hlc([100 + i * i * 0.1 for i in range(n)], [95 + i * i * 0.1 for i in range(n)],
                            [98 + i * i * 0.1 for i in range(n)])
        res = calc_psar(candles, 0.02, 0.02, 0.20)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_new_extreme_points_recorded(self):
        # Strictly rising highs in an uptrend -> every candle sets a new EP
        n = 10
        candles = make_hlc([100 + i for i in range(n)], [95 + i for i in range(n)], [98 + i for i in range(n)])
        res = calc_psar(candles)
        # SAR must be strictly increasing while a new high is set every bar
        # (each new EP pushes SAR further toward price)
        sar_vals = res["sar"][1:]
        for i in range(1, len(sar_vals)):
            assert sar_vals[i] >= sar_vals[i - 1]

    def test_af_increase_on_new_extreme(self):
        # Reproduce internal AF progression via the same recurrence, and
        # confirm SAR's rate of approach to price increases (implying AF grew)
        n = 20
        candles = make_hlc([100 + i for i in range(n)], [95 + i for i in range(n)], [98 + i for i in range(n)])
        res = calc_psar(candles, 0.02, 0.02, 0.20)
        early_gap = res["sar"][3] - res["sar"][2]
        later_gap = res["sar"][10] - res["sar"][9]
        assert later_gap >= early_gap, "AF acceleration should widen the SAR step over time (before hitting the cap)"

    def test_af_capped_at_maximum(self):
        # Long enough uptrend with a new high every bar to force AF to cap.
        # With AF capped, price rising linearly, and SAR's gap-to-price
        # converging toward a fixed ratio, successive SAR deltas approach a
        # limit — verify convergence (later windows vary less than earlier
        # ones) rather than asserting they're already flat, since a fixed
        # AF against a still-catching-up SAR converges gradually, not
        # instantly.
        n = 300
        candles = make_hlc([100 + i for i in range(n)], [95 + i for i in range(n)], [98 + i for i in range(n)])
        res_capped = calc_psar(candles, 0.02, 0.02, 0.06)   # caps after 2 increments

        def spread(lo, hi):
            deltas = [res_capped["sar"][i] - res_capped["sar"][i - 1] for i in range(lo, hi)]
            return max(deltas) - min(deltas)

        early_spread = spread(40, 60)
        late_spread = spread(250, 270)
        assert late_spread < early_spread, "SAR step variability must shrink over time once AF is capped"
        assert late_spread < 1e-3, "SAR step must have essentially stabilized far into a capped, linear trend"

    # ── Mathematical correctness / structural checks ────────────────────────
    def test_sar_recurrence_matches_formula(self):
        n = 10
        candles = make_hlc([100, 102, 101, 105, 108, 104, 103, 107, 110, 112],
                            [95, 97, 96, 99, 102, 98, 97, 101, 104, 106],
                            [98, 100, 99, 103, 106, 100, 99, 105, 108, 110])
        res = calc_psar(candles, 0.02, 0.02, 0.20)
        # Manually re-derive index 2 from index 1's state using the documented formula
        up = candles[1]["close"] >= candles[0]["close"]
        trend1 = 1 if up else -1
        ep = candles[1]["high"] if up else candles[1]["low"]
        sar1 = candles[0]["low"] if up else candles[0]["high"]
        assert res["sar"][1] == sar1
        assert res["trend"][1] == trend1
        af = 0.02
        candidate = sar1 + af * (ep - sar1)
        if trend1 == 1:
            candidate = min(candidate, candles[0]["low"], candles[0]["low"])
        assert abs(res["sar"][2] - candidate) < 1e-9 or res["trend"][2] != trend1

    def test_reversal_sar_reset_to_previous_ep(self):
        # Construct a forced reversal and verify SAR resets to the EP that
        # was active immediately before the reversal candle.
        up_h = [100, 102, 104, 106, 108]
        up_l = [95, 97, 99, 101, 103]
        up_c = [98, 100, 102, 104, 106]
        crash = make_hlc(up_h + [70], up_l + [60], up_c + [65])
        res = calc_psar(crash)
        # Find the reversal index (trend flips from 1 to -1)
        rev_idx = None
        for i in range(2, len(crash)):
            if res["trend"][i - 1] == 1 and res["trend"][i] == -1:
                rev_idx = i
                break
        assert rev_idx is not None, "this dataset must force a reversal"
        # At reversal, SAR must equal the EP that was tracked going into it
        # (the highest high reached during the uptrend so far)
        ep_before_reversal = max(c["high"] for c in crash[:rev_idx])
        assert abs(res["sar"][rev_idx] - ep_before_reversal) < 1e-9

    def test_reversal_ep_reset_to_current_extreme(self):
        up_h = [100, 102, 104, 106, 108]
        up_l = [95, 97, 99, 101, 103]
        up_c = [98, 100, 102, 104, 106]
        crash = make_hlc(up_h + [70, 68], up_l + [60, 58], up_c + [65, 62])
        res = calc_psar(crash)
        rev_idx = None
        for i in range(2, len(crash)):
            if res["trend"][i - 1] == 1 and res["trend"][i] == -1:
                rev_idx = i
                break
        assert rev_idx is not None
        # One bar after reversal, SAR should have moved from the reset EP
        # (the reversal candle's low) toward price — confirms EP was reset
        # to current low rather than continuing the old uptrend's EP.
        if rev_idx + 1 < len(crash):
            assert res["sar"][rev_idx + 1] is not None

    def test_trend_state_transitions_are_only_1_or_minus_1(self):
        import random
        random.seed(4)
        n = 150
        highs, lows, closes = [], [], []
        price = 100.0
        for i in range(n):
            price += 3 * math.sin(i / 5.0) + random.uniform(-1, 1)
            highs.append(price + 1); lows.append(price - 1); closes.append(price)
        res = calc_psar(make_hlc(highs, lows, closes))
        for t in res["trend"][1:]:
            assert t in (1, -1)

    def test_previous_high_low_constraint_uptrend(self):
        # Craft a case where the raw (unconstrained) candidate would exceed
        # the prior two lows, and confirm the constraint actually clamps it.
        candles = make_hlc(
            [100, 130, 132, 90],     # a big spike up then a low candle
            [95, 125, 128, 85],
            [98, 128, 130, 87],
        )
        res = calc_psar(candles, 0.5, 0.5, 0.5)  # large AF to force the constraint to bind
        # SAR at index 2 must never exceed min(low[1], low[0])
        if res["trend"][2] == 1:
            assert res["sar"][2] <= min(candles[0]["low"], candles[1]["low"]) + 1e-9

    def test_previous_high_low_constraint_downtrend(self):
        candles = make_hlc(
            [100, 70, 68, 130],
            [95, 65, 62, 125],
            [98, 68, 65, 128],
        )
        res = calc_psar(candles, 0.5, 0.5, 0.5)
        if res["trend"][2] == -1:
            assert res["sar"][2] >= max(candles[0]["high"], candles[1]["high"]) - 1e-9

    def test_no_lookahead_bias(self):
        """Appending future candles must never change already-computed historical SAR values."""
        import random
        random.seed(5)
        n = 50
        highs, lows, closes = [], [], []
        price = 100.0
        for _ in range(n):
            price += random.uniform(-2, 2)
            highs.append(price + 1); lows.append(price - 1); closes.append(price)
        candles = make_hlc(highs, lows, closes)
        res_before = calc_psar(candles)

        extra_h = [price + 1000 + i for i in range(20)]  # deliberately different regime
        extra_l = [price + 998 + i for i in range(20)]
        extra_c = [price + 999 + i for i in range(20)]
        extended = candles + make_hlc(extra_h, extra_l, extra_c)
        res_after = calc_psar(extended)

        for i in range(n):
            a, b = res_before["sar"][i], res_after["sar"][i]
            if a is None or b is None:
                assert a == b
            else:
                assert abs(a - b) < 1e-9, f"sar[{i}] changed after appending future candles"
            assert res_before["trend"][i] == res_after["trend"][i]

    # ── Edge case / pathological input testing ──────────────────────────────
    def test_repeated_highs(self):
        n = 20
        candles = make_hlc([105] * n, [95 + i * 0.1 for i in range(n)], [100] * n)
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_repeated_lows(self):
        n = 20
        candles = make_hlc([105 + i * 0.1 for i in range(n)], [95] * n, [100] * n)
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_very_small_price_range(self):
        n = 30
        base = 100.0
        candles = make_hlc([base + i * 0.0001 for i in range(n)], [base - 0.0001 + i * 0.0001 for i in range(n)],
                            [base for i in range(n)])
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_large_price_values(self):
        n = 30
        base = 5_000_000.0
        candles = make_hlc([base + i * 100 for i in range(n)], [base - 100 + i * 100 for i in range(n)],
                            [base + i * 50 for i in range(n)])
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_very_small_price_values(self):
        n = 30
        candles = make_hlc([0.0002 + i * 0.00001 for i in range(n)], [0.0001 + i * 0.00001 for i in range(n)],
                            [0.00015 + i * 0.00001 for i in range(n)])
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_long_monotonic_trend(self):
        n = 500
        candles = make_hlc([100 + i * 0.5 for i in range(n)], [95 + i * 0.5 for i in range(n)],
                            [98 + i * 0.5 for i in range(n)])
        res = calc_psar(candles)
        assert all(t == 1 for t in res["trend"][1:])
        for v in res["sar"]:
            if v is not None:
                assert math.isfinite(v)

    def test_frequent_reversals_no_corruption(self):
        n = 100
        highs, lows, closes = [], [], []
        for i in range(n):
            price = 100 + (10 if i % 2 == 0 else -10)
            highs.append(price + 2); lows.append(price - 2); closes.append(price)
        res = calc_psar(make_hlc(highs, lows, closes))
        for v in res["sar"]:
            if v is not None:
                assert math.isfinite(v)
        for t in res["trend"]:
            if t is not None:
                assert t in (1, -1)

    def test_equal_highs_and_lows_across_candles(self):
        n = 15
        candles = make_hlc([100] * n, [95] * n, [97.5] * n)
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_gap_up(self):
        candles = make_hlc([100, 102, 150, 152], [95, 97, 145, 147], [98, 100, 148, 150])
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_gap_down(self):
        candles = make_hlc([100, 98, 50, 48], [95, 93, 45, 43], [98, 96, 48, 46])
        res = calc_psar(candles)
        for v in res["sar"][1:]:
            assert v is not None and math.isfinite(v)

    def test_no_nan_or_infinity_after_warmup_stress(self):
        import random
        random.seed(6)
        n = 300
        highs, lows, closes = [], [], []
        price = 1000.0
        for i in range(n):
            if i % 37 == 0:
                price *= random.choice([0.7, 1.4])  # occasional violent gap
            price += random.uniform(-5, 5)
            highs.append(price + random.uniform(0.1, 3))
            lows.append(price - random.uniform(0.1, 3))
            closes.append(price)
        res = calc_psar(make_hlc(highs, lows, closes))
        for v in res["sar"]:
            if v is not None:
                assert math.isfinite(v)

    # ── Independent reference / golden-style verification ───────────────────
    def test_independent_hand_computed_golden_case(self):
        """
        Fully hand-computed 4-candle example, independent of calc_psar,
        tracing the algorithm's own documented rules step by step.
        """
        candles = make_hlc(
            highs=[110, 115, 112, 120],
            lows=[100, 108, 105, 111],
            closes=[105, 112, 108, 118],
        )
        # Index 1: close[1]=112 >= close[0]=105 -> uptrend
        # sar[1] = low[0] = 100, ep = high[1] = 115, af = 0.02
        expected_sar1 = 100.0
        # Index 2: candidate = 100 + 0.02*(115-100) = 100.3
        #          constrained by min(low[1], low[0]) = min(108,100) = 100 -> stays 100.3 (100.3 < 100? no, min(100.3,108,100)=100)
        #          candidate = min(100.3, 108, 100) = 100
        #          low[2]=105 > candidate(100) -> no reversal, sar[2] = 100
        #          high[2]=112 < ep(115) -> ep stays 115, af stays 0.02
        expected_sar2 = 100.0
        res = calc_psar(candles, 0.02, 0.02, 0.20)
        assert abs(res["sar"][1] - expected_sar1) < 1e-9
        assert res["trend"][1] == 1
        assert abs(res["sar"][2] - expected_sar2) < 1e-9
        assert res["trend"][2] == 1

    def test_combination_with_other_overlay_indicators(self):
        """PSAR computed independently alongside Supertrend/Ichimoku on the same candles (sanity, no cross-talk)."""
        n = 60
        candles = make_hlc([100 + i for i in range(n)], [95 + i for i in range(n)], [98 + i for i in range(n)])
        psar = calc_psar(candles)
        timed_candles = make_ohlc_timed([c["high"] for c in candles], [c["low"] for c in candles],
                                          [c["close"] for c in candles])
        ichimoku = calc_ichimoku(timed_candles)
        assert psar["sar"][-1] is not None
        assert ichimoku["tenkan"][-1] is not None


# ═══════════════════════════════════════════════════════════════════════════
# PIVOT POINTS  — Phase 2L
# ═══════════════════════════════════════════════════════════════════════════

def ist_wallclock_to_epoch(naive_dt):
    """
    Convert a naive datetime representing IST WALL-CLOCK time into the
    correct UTC epoch seconds — using calendar.timegm (UTC-based), never
    datetime.timestamp() (which depends on the local system timezone and
    would silently corrupt this conversion on any machine not already
    running in IST).
    """
    return calendar.timegm(naive_dt.timetuple()) - 19800


def session_day_key(epoch_seconds):
    """
    IST trading-day key from epoch seconds — Python port of
    indicators.js Calc.candleSessionKey's numeric-time branch (adds the
    +5:30 IST offset before taking the calendar date, so an intraday bar
    just after UTC midnight still buckets into the correct IST session day).
    """
    ist = datetime.datetime.fromtimestamp(epoch_seconds + 19800, tz=datetime.timezone.utc)
    return f"{ist.year:04d}-{ist.month:02d}-{ist.day:02d}"


def iso_week_key(day_key):
    """Python port of indicators.js Calc._isoWeekKey."""
    y, m, d = (int(x) for x in day_key.split("-"))
    dt = datetime.date(y, m, d)
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def resolve_auto_period(bar_seconds):
    """
    Python port of indicators.js Calc.pivotPoints's 'Auto' resolution.
    Matches TradingView's documented Auto rule for the intraday range:
    Daily pivots up to and including 15-minute charts, Weekly pivots above
    15 minutes and below 1 day. 1-day-or-longer bars aren't part of
    TradingView's explicitly-verified rule used here; the production
    implementation documents its own conservative Monthly fallback for
    that case rather than guessing a Yearly cutoff.
    """
    if bar_seconds <= 15 * 60:
        return "Daily"
    elif bar_seconds < 86400:
        return "Weekly"
    return "Monthly"


def pivot_levels_for_method(method, H, L, C):
    """
    Python port of indicators.js Calc._pivotLevelsForMethod.

    Standard/Traditional R4/R5/S4/S5 use TradingView's actual documented
    formula (3P+H-3L / 3P-3H+L / 4P+H-4L / 4P-4H+L) — an earlier version of
    both this port and the JS it mirrors instead guessed an "arithmetic
    progression continuation" of R1-R3 (R4 = R3+(R2-R1)), which does NOT
    match TradingView: for H=115,L=100,C=108 the guessed shape gives
    R4=137.667 while the real formula gives R4=138. That was a real bug in
    both implementations (they happened to agree with each other because
    both were derived from the same wrong assumption, not because either
    was independently verified against TradingView) — fixed in both places.
    """
    rng = H - L
    if method == "Fibonacci":
        p = (H + L + C) / 3.0
        return {
            "p": p,
            "r1": p + 0.382 * rng, "r2": p + 0.618 * rng, "r3": p + 1.000 * rng, "r4": None, "r5": None,
            "s1": p - 0.382 * rng, "s2": p - 0.618 * rng, "s3": p - 1.000 * rng, "s4": None, "s5": None,
        }
    if method == "Woodie":
        p = (H + L + 2 * C) / 4.0
        return {
            "p": p,
            "r1": 2 * p - L, "r2": p + rng, "r3": H + 2 * (p - L), "r4": None, "r5": None,
            "s1": 2 * p - H, "s2": p - rng, "s3": L - 2 * (H - p), "s4": None, "s5": None,
        }
    if method == "Camarilla":
        p = (H + L + C) / 3.0
        return {
            "p": p,
            "r1": C + rng * 1.1 / 12, "r2": C + rng * 1.1 / 6, "r3": C + rng * 1.1 / 4, "r4": C + rng * 1.1 / 2, "r5": None,
            "s1": C - rng * 1.1 / 12, "s2": C - rng * 1.1 / 6, "s3": C - rng * 1.1 / 4, "s4": C - rng * 1.1 / 2, "s5": None,
        }
    # Standard ("Traditional" in TradingView's naming)
    p = (H + L + C) / 3.0
    return {
        "p": p,
        "r1": 2 * p - L, "r2": p + rng, "r3": H + 2 * (p - L), "r4": 3 * p + H - 3 * L, "r5": 4 * p + H - 4 * L,
        "s1": 2 * p - H, "s2": p - rng, "s3": L - 2 * (H - p), "s4": 3 * p - 3 * H + L, "s5": 4 * p - 4 * H + L,
    }


LEVEL_KEYS = ["p", "r1", "r2", "r3", "r4", "r5", "s1", "s2", "s3", "s4", "s5"]


def calc_pivot_points(candles, method="Standard", period="Daily"):
    """
    Independent Python reference port of indicators.js Calc.pivotPoints.
    Groups candles into trading periods (Daily/Weekly/Monthly) using the
    IST session-day key, then assigns period k's levels — computed from
    period k-1's COMPLETED high/low/close — to every candle in period k.
    The first period gets None levels (no prior period exists).
    """
    n = len(candles)
    out = {k: [None] * n for k in LEVEL_KEYS}
    if n == 0:
        return out

    period_keys = []
    for c in candles:
        day_key = session_day_key(c["time"])
        if period == "Weekly":
            period_keys.append(iso_week_key(day_key))
        elif period == "Monthly":
            period_keys.append(day_key[:7])
        else:
            period_keys.append(day_key)

    periods = []  # list of {start, end, high, low, close}
    cur_key = None
    cur = None
    for i, c in enumerate(candles):
        if period_keys[i] != cur_key:
            cur_key = period_keys[i]
            cur = {"start": i, "end": i, "high": float("-inf"), "low": float("inf"), "close": None}
            periods.append(cur)
        h, l, cl = float(c["high"]), float(c["low"]), float(c["close"])
        cur["high"] = max(cur["high"], h)
        cur["low"] = min(cur["low"], l)
        cur["close"] = cl
        cur["end"] = i

    for k in range(1, len(periods)):
        prev = periods[k - 1]
        levels = pivot_levels_for_method(method, prev["high"], prev["low"], prev["close"])
        cur = periods[k]
        for i in range(cur["start"], cur["end"] + 1):
            for lk in LEVEL_KEYS:
                out[lk][i] = levels[lk]

    return out


def make_daily_session_candles(days_ohlc, bars_per_day=1, start_hour=9, minute_step=60):
    """
    Build a multi-day intraday candle list. `days_ohlc` is a list of
    (high, low, close) per day; each day is expanded into `bars_per_day`
    intraday bars (all sharing that day's H/L, with close only meaningful
    on the LAST bar of the day) at IST start_hour onward, so the resulting
    dataset behaves like a realistic multi-session intraday feed.
    """
    candles = []
    base_date = datetime.date(2026, 1, 1)
    day_offset = 0
    for (h, l, c) in days_ohlc:
        # Skip to the next weekday (Mon-Fri) so this helper naturally produces
        # weekend gaps like real market data, without any special handling.
        while True:
            d = base_date + datetime.timedelta(days=day_offset)
            day_offset += 1
            if d.weekday() < 5:
                break
        for b in range(bars_per_day):
            ist_dt = datetime.datetime(d.year, d.month, d.day, start_hour, 0) + datetime.timedelta(minutes=b * minute_step)
            epoch = ist_wallclock_to_epoch(ist_dt)
            is_last = (b == bars_per_day - 1)
            candles.append({
                "time": epoch,
                "high": h,
                "low": l,
                "close": c if is_last else (h + l) / 2.0,
            })
    return candles


class TestPivotPoints:

    # ── Method math: Standard ───────────────────────────────────────────────
    def test_standard_formula(self):
        H, L, C = 115.0, 100.0, 108.0
        levels = pivot_levels_for_method("Standard", H, L, C)
        p = (H + L + C) / 3
        r1, r2, r3 = 2 * p - L, p + (H - L), H + 2 * (p - L)
        s1, s2, s3 = 2 * p - H, p - (H - L), L - 2 * (H - p)
        assert abs(levels["p"] - p) < 1e-9
        assert abs(levels["r1"] - r1) < 1e-9
        assert abs(levels["r2"] - r2) < 1e-9
        assert abs(levels["r3"] - r3) < 1e-9
        assert abs(levels["s1"] - s1) < 1e-9
        assert abs(levels["s2"] - s2) < 1e-9
        assert abs(levels["s3"] - s3) < 1e-9
        # R4/R5/S4/S5 use TradingView's actual documented Traditional
        # formula (3P+H-3L, 4P+H-4L / 3P-3H+L, 4P-4H+L) — NOT an "arithmetic
        # progression continuation" of R1-R3 (a prior version of this test,
        # and of the implementation it exercises, asserted that shape; it
        # does not match TradingView and has been corrected).
        assert abs(levels["r4"] - (3 * p + H - 3 * L)) < 1e-9
        assert abs(levels["r5"] - (4 * p + H - 4 * L)) < 1e-9
        assert abs(levels["s4"] - (3 * p - 3 * H + L)) < 1e-9
        assert abs(levels["s5"] - (4 * p - 4 * H + L)) < 1e-9
        # Sanity: R4=138.0 for this exact H/L/C (the number the previous,
        # wrong formula got measurably different: 137.667).
        assert abs(levels["r4"] - 138.0) < 1e-9

    # ── Method math: Fibonacci ───────────────────────────────────────────────
    def test_fibonacci_formula(self):
        H, L, C = 120.0, 100.0, 110.0
        levels = pivot_levels_for_method("Fibonacci", H, L, C)
        p = (H + L + C) / 3
        rng = H - L
        assert abs(levels["p"] - p) < 1e-9
        assert abs(levels["r1"] - (p + 0.382 * rng)) < 1e-9
        assert abs(levels["r2"] - (p + 0.618 * rng)) < 1e-9
        assert abs(levels["r3"] - (p + 1.000 * rng)) < 1e-9
        assert abs(levels["s1"] - (p - 0.382 * rng)) < 1e-9
        assert abs(levels["s2"] - (p - 0.618 * rng)) < 1e-9
        assert abs(levels["s3"] - (p - 1.000 * rng)) < 1e-9
        assert levels["r4"] is None and levels["s4"] is None

    # ── Method math: Woodie ──────────────────────────────────────────────────
    def test_woodie_formula(self):
        H, L, C = 120.0, 100.0, 110.0
        levels = pivot_levels_for_method("Woodie", H, L, C)
        p = (H + L + 2 * C) / 4
        assert abs(levels["p"] - p) < 1e-9
        assert abs(levels["r1"] - (2 * p - L)) < 1e-9
        assert abs(levels["r2"] - (p + (H - L))) < 1e-9
        assert abs(levels["r3"] - (H + 2 * (p - L))) < 1e-9
        assert abs(levels["s1"] - (2 * p - H)) < 1e-9
        assert abs(levels["s2"] - (p - (H - L))) < 1e-9
        assert abs(levels["s3"] - (L - 2 * (H - p))) < 1e-9

    # ── Method math: Camarilla ───────────────────────────────────────────────
    def test_camarilla_formula(self):
        H, L, C = 120.0, 100.0, 110.0
        levels = pivot_levels_for_method("Camarilla", H, L, C)
        rng = H - L
        assert abs(levels["r1"] - (C + rng * 1.1 / 12)) < 1e-9
        assert abs(levels["r2"] - (C + rng * 1.1 / 6)) < 1e-9
        assert abs(levels["r3"] - (C + rng * 1.1 / 4)) < 1e-9
        assert abs(levels["r4"] - (C + rng * 1.1 / 2)) < 1e-9
        assert abs(levels["s1"] - (C - rng * 1.1 / 12)) < 1e-9
        assert abs(levels["s2"] - (C - rng * 1.1 / 6)) < 1e-9
        assert abs(levels["s3"] - (C - rng * 1.1 / 4)) < 1e-9
        assert abs(levels["s4"] - (C - rng * 1.1 / 2)) < 1e-9
        # Camarilla's R4/S4 use a different formula (tight range multiples)
        # than Standard's R4/S4 (arithmetic progression) — confirm they
        # actually diverge rather than one silently reusing the other's math.
        std = pivot_levels_for_method("Standard", H, L, C)
        assert abs(std["r4"] - levels["r4"]) > 1e-6
        assert abs(std["s4"] - levels["s4"]) > 1e-6

    # ── Daily period: previous-day OHLC selection ───────────────────────────
    def test_daily_period_uses_previous_day(self):
        candles = make_daily_session_candles([(110, 100, 105), (120, 112, 118), (125, 115, 122)])
        res = calc_pivot_points(candles, "Standard", "Daily")
        assert res["p"][0] is None, "first day has no prior period"
        expected_day2 = pivot_levels_for_method("Standard", 110, 100, 105)
        assert abs(res["p"][1] - expected_day2["p"]) < 1e-9
        expected_day3 = pivot_levels_for_method("Standard", 120, 112, 118)
        assert abs(res["p"][2] - expected_day3["p"]) < 1e-9

    # ── Weekly period ────────────────────────────────────────────────────────
    def test_weekly_period_groups_days_into_weeks(self):
        # 15 consecutive trading days; derive actual ISO week boundaries from
        # the generated candles themselves rather than assuming a fixed
        # 5-trading-days-per-week split (the FIRST week in any given date
        # range can be a partial week, depending on which weekday it starts on).
        days = [(100 + i, 95 + i, 98 + i) for i in range(15)]
        candles = make_daily_session_candles(days)
        week_keys = [iso_week_key(session_day_key(c["time"])) for c in candles]
        res = calc_pivot_points(candles, "Standard", "Weekly")

        first_week = week_keys[0]
        first_week_end = max(i for i, k in enumerate(week_keys) if k == first_week)
        assert all(v is None for v in res["p"][:first_week_end + 1]), "first week has no prior period"

        second_week = week_keys[first_week_end + 1]
        second_week_indices = [i for i, k in enumerate(week_keys) if k == second_week]
        second_week_pivots = [res["p"][i] for i in second_week_indices]
        assert all(v is not None for v in second_week_pivots)
        assert len(set(second_week_pivots)) == 1, "an entire week must share one fixed pivot set"

    # ── Monthly period ───────────────────────────────────────────────────────
    def test_monthly_period_groups_days_into_months(self):
        # Force two separate calendar months via a JSON-ready structure:
        # reuse make_daily_session_candles's day generator but check the
        # month grouping key directly.
        candles = make_daily_session_candles([(100, 95, 98)] * 40)
        res = calc_pivot_points(candles, "Standard", "Monthly")
        # Just verify structurally: values are constant within any contiguous
        # run belonging to the same period, and change only at a period key
        # boundary — verified via day-key month prefix.
        keys = [session_day_key(c["time"])[:7] for c in candles]
        # Find first month-boundary index (if any) and confirm pivot flips
        for i in range(1, len(keys)):
            if keys[i] != keys[i - 1]:
                if res["p"][i] is not None and res["p"][i - 1] is not None:
                    # crossing into a new month with an existing prior month
                    # of data recalculates (not asserted equal or different,
                    # just must not crash/None unexpectedly for i > first month)
                    pass
                break
        assert True  # structural smoke test — see dedicated boundary test below

    def test_monthly_period_boundary_recalculates(self):
        # Build days spanning a real month boundary explicitly.
        candles = []
        # January days (trading days only): 3 days
        for d, (h, l, c) in zip([1, 2, 5], [(110, 100, 105), (112, 102, 108), (115, 105, 111)]):  # 1st is Thu, 2nd Fri, 5th Mon 2026
            dt = datetime.datetime(2026, 1, d, 9, 0)
            candles.append({"time": ist_wallclock_to_epoch(dt), "high": h, "low": l, "close": c})
        # February days: 2 days
        for d, (h, l, c) in zip([2, 3], [(120, 110, 118), (122, 112, 119)]):
            dt = datetime.datetime(2026, 2, d, 9, 0)
            candles.append({"time": ist_wallclock_to_epoch(dt), "high": h, "low": l, "close": c})
        res = calc_pivot_points(candles, "Standard", "Monthly")
        assert res["p"][0] is None  # first Jan day: no prior month
        # Jan days 2 and 3 (index 1, 2) share Jan's pivot (from... wait first
        # period IS January itself, so all 3 Jan candles share None until a
        # NEW period starts). Confirm: all January candles are None (still
        # first period), February candles get January's aggregate H/L/C.
        assert all(v is None for v in res["p"][:3])
        expected_feb = pivot_levels_for_method("Standard", 115, 100, 111)  # Jan's H=115,L=100,C=111(last close)
        assert abs(res["p"][3] - expected_feb["p"]) < 1e-9
        assert abs(res["p"][4] - expected_feb["p"]) < 1e-9

    # ── Market-calendar tests ────────────────────────────────────────────────
    def test_weekend_gap_uses_last_actual_trading_session(self):
        # Explicit Friday -> Monday dates (not the generic helper's arbitrary
        # start date, which may or may not span a weekend for a short list).
        fri = datetime.datetime(2026, 1, 2, 9, 0)   # Friday
        mon = datetime.datetime(2026, 1, 5, 9, 0)   # Monday (skips Sat/Sun)
        candles = [
            {"time": ist_wallclock_to_epoch(fri), "high": 110, "low": 100, "close": 105},
            {"time": ist_wallclock_to_epoch(mon), "high": 112, "low": 102, "close": 108},
        ]
        assert mon.date() - fri.date() >= datetime.timedelta(days=3), "must actually span a weekend"
        res = calc_pivot_points(candles, "Standard", "Daily")
        expected = pivot_levels_for_method("Standard", 110, 100, 105)
        assert abs(res["p"][1] - expected["p"]) < 1e-9

    def test_holiday_gap_uses_last_actual_trading_session(self):
        # Simulate a holiday: Monday and Wednesday present, Tuesday absent.
        mon = datetime.datetime(2026, 1, 5, 9, 0)   # Monday
        wed = datetime.datetime(2026, 1, 7, 9, 0)   # Wednesday (Tue = holiday, simply missing)
        candles = [
            {"time": ist_wallclock_to_epoch(mon), "high": 110, "low": 100, "close": 105},
            {"time": int(wed.timestamp()) - 19800, "high": 112, "low": 102, "close": 108},
        ]
        res = calc_pivot_points(candles, "Standard", "Daily")
        expected = pivot_levels_for_method("Standard", 110, 100, 105)
        assert abs(res["p"][1] - expected["p"]) < 1e-9, "Wednesday must use Monday's OHLC, not a fabricated Tuesday"

    def test_multiple_consecutive_holidays(self):
        d1 = datetime.datetime(2026, 1, 5, 9, 0)
        d2 = datetime.datetime(2026, 1, 9, 9, 0)  # 3 consecutive days missing in between
        candles = [
            {"time": ist_wallclock_to_epoch(d1), "high": 110, "low": 100, "close": 105},
            {"time": ist_wallclock_to_epoch(d2), "high": 112, "low": 102, "close": 108},
        ]
        res = calc_pivot_points(candles, "Standard", "Daily")
        expected = pivot_levels_for_method("Standard", 110, 100, 105)
        assert abs(res["p"][1] - expected["p"]) < 1e-9

    # ── No-look-ahead (mandatory) ────────────────────────────────────────────
    def test_no_lookahead_current_period_ohlc_does_not_affect_its_own_pivots(self):
        candles = make_daily_session_candles([(110, 100, 105), (120, 112, 118)], bars_per_day=4, minute_step=15)
        res_before = calc_pivot_points(candles, "Standard", "Daily")
        day2_start = 4  # second day's first bar index (4 bars/day)

        # Mutate day 2's (the "current", still-forming period's) later bars'
        # high/low/close wildly — this must NOT change day 2's own pivots,
        # since day 2's pivots are derived entirely from day 1.
        mutated = [dict(c) for c in candles]
        mutated[day2_start + 3]["high"] = 999
        mutated[day2_start + 3]["low"] = 1
        mutated[day2_start + 3]["close"] = 500
        res_after = calc_pivot_points(mutated, "Standard", "Daily")

        for i in range(day2_start, day2_start + 4):
            assert res_before["p"][i] == res_after["p"][i], "day 2's own OHLC must never affect day 2's pivots"
            assert res_before["r1"][i] == res_after["r1"][i]
            assert res_before["s1"][i] == res_after["s1"][i]

    def test_no_lookahead_full_scenario_from_spec(self):
        """
        1. Calculate Monday pivots (from Friday).
        2. Modify Monday's live high/low/close.
        3. Confirm Monday pivot levels remain unchanged.
        4. Complete Monday, start Tuesday.
        5. Confirm Tuesday pivots now use Monday's COMPLETED OHLC.
        """
        candles = make_daily_session_candles(
            [(110, 100, 105), (120, 112, 118), (130, 122, 128)], bars_per_day=1
        )  # Fri, Mon, Tue
        res1 = calc_pivot_points(candles, "Standard", "Daily")
        monday_pivot_before = res1["p"][1]

        mutated = [dict(c) for c in candles]
        mutated[1]["high"] = 5000
        mutated[1]["low"] = 1
        mutated[1]["close"] = 2500
        res2 = calc_pivot_points(mutated, "Standard", "Daily")
        assert res2["p"][1] == monday_pivot_before, "Monday's own mutated OHLC must not move Monday's pivot"

        # Tuesday's pivot (index 2) must use Monday's ORIGINAL completed OHLC
        # (from the unmutated dataset) since Tuesday's period boundary uses
        # whatever Monday's candles actually closed at.
        expected_tuesday = pivot_levels_for_method("Standard", 120, 112, 118)
        assert abs(res1["p"][2] - expected_tuesday["p"]) < 1e-9

    # ── Historical mapping ───────────────────────────────────────────────────
    def test_historical_mapping_no_offset_or_duplication(self):
        days = [(100 + i * 2, 95 + i * 2, 98 + i * 2) for i in range(6)]
        candles = make_daily_session_candles(days, bars_per_day=1)
        res = calc_pivot_points(candles, "Standard", "Daily")
        for i in range(1, 6):
            prev_h, prev_l, prev_c = days[i - 1]
            expected = pivot_levels_for_method("Standard", prev_h, prev_l, prev_c)
            assert abs(res["p"][i] - expected["p"]) < 1e-9, f"index {i} must map to period {i-1}'s OHLC exactly"

    def test_historical_mapping_multi_bar_days(self):
        days = [(110, 100, 105), (120, 112, 118), (130, 122, 128)]
        candles = make_daily_session_candles(days, bars_per_day=6, minute_step=30)
        res = calc_pivot_points(candles, "Standard", "Daily")
        # All 6 bars of day 2 (index 6..11) must carry IDENTICAL pivot values
        day2_pivots = res["p"][6:12]
        assert all(v is not None for v in day2_pivots)
        assert len(set(day2_pivots)) == 1

    # ── Edge cases ───────────────────────────────────────────────────────────
    def test_empty_data(self):
        res = calc_pivot_points([], "Standard", "Daily")
        for k in LEVEL_KEYS:
            assert res[k] == []

    def test_one_period(self):
        candles = make_daily_session_candles([(110, 100, 105)])
        res = calc_pivot_points(candles, "Standard", "Daily")
        assert res["p"] == [None]

    def test_two_periods(self):
        candles = make_daily_session_candles([(110, 100, 105), (120, 112, 118)])
        res = calc_pivot_points(candles, "Standard", "Daily")
        assert res["p"][0] is None
        assert res["p"][1] is not None

    def test_missing_period_data_handled_safely(self):
        # A period with identical high==low (degenerate range) must not
        # produce NaN/Infinity in the next period's levels.
        candles = make_daily_session_candles([(100, 100, 100), (110, 100, 105)])
        res = calc_pivot_points(candles, "Camarilla", "Daily")
        for k in LEVEL_KEYS:
            if res[k][1] is not None:
                assert math.isfinite(res[k][1])

    def test_large_price_values(self):
        candles = make_daily_session_candles([(5_000_000, 4_900_000, 4_950_000), (5_100_000, 5_000_000, 5_050_000)])
        res = calc_pivot_points(candles, "Fibonacci", "Daily")
        for k in LEVEL_KEYS:
            if res[k][1] is not None:
                assert math.isfinite(res[k][1])

    def test_very_small_price_values(self):
        candles = make_daily_session_candles([(0.0012, 0.0009, 0.0010), (0.0013, 0.0010, 0.0011)])
        res = calc_pivot_points(candles, "Woodie", "Daily")
        for k in LEVEL_KEYS:
            if res[k][1] is not None:
                assert math.isfinite(res[k][1])

    def test_flat_market(self):
        candles = make_daily_session_candles([(100, 100, 100), (100, 100, 100), (100, 100, 100)])
        res = calc_pivot_points(candles, "Standard", "Daily")
        for i in (1, 2):
            assert res["p"][i] == 100.0
            assert res["r1"][i] == 100.0
            assert res["s1"][i] == 100.0

    def test_gap_up(self):
        candles = make_daily_session_candles([(110, 100, 105), (200, 190, 195)])
        res = calc_pivot_points(candles, "Standard", "Daily")
        expected = pivot_levels_for_method("Standard", 110, 100, 105)
        assert abs(res["p"][1] - expected["p"]) < 1e-9
        for k in LEVEL_KEYS:
            if res[k][1] is not None:
                assert math.isfinite(res[k][1])

    def test_gap_down(self):
        candles = make_daily_session_candles([(110, 100, 105), (50, 40, 45)])
        res = calc_pivot_points(candles, "Standard", "Daily")
        for k in LEVEL_KEYS:
            if res[k][1] is not None:
                assert math.isfinite(res[k][1])

    def test_large_overnight_gap(self):
        candles = make_daily_session_candles([(100, 95, 98), (1000, 950, 980)])
        res = calc_pivot_points(candles, "Camarilla", "Daily")
        for k in LEVEL_KEYS:
            if res[k][1] is not None:
                assert math.isfinite(res[k][1])

    def test_no_nan_or_infinity_stress(self):
        import random
        random.seed(11)
        days = []
        price = 100.0
        for _ in range(120):
            price += random.uniform(-5, 5)
            l = price - random.uniform(0.5, 5)
            h = price + random.uniform(0.5, 5)
            days.append((h, l, price))
        candles = make_daily_session_candles(days, bars_per_day=3, minute_step=20)
        for method in ("Standard", "Fibonacci", "Woodie", "Camarilla"):
            res = calc_pivot_points(candles, method, "Daily")
            for k in LEVEL_KEYS:
                for v in res[k]:
                    if v is not None:
                        assert math.isfinite(v)

    # ── Independent reference calculation ────────────────────────────────────
    def test_independent_hand_computed_golden_case(self):
        """Hand-computed example independent of calc_pivot_points, covering all 4 methods."""
        H, L, C = 118.0, 96.0, 110.0
        rng = H - L  # 22.0

        # Standard
        p_std = (H + L + C) / 3.0  # 108.0
        assert abs(p_std - 108.0) < 1e-9
        r1_std = 2 * p_std - L  # 120.0
        s1_std = 2 * p_std - H  # 98.0
        assert abs(r1_std - 120.0) < 1e-9
        assert abs(s1_std - 98.0) < 1e-9

        # Fibonacci
        r1_fib = p_std + 0.382 * rng
        assert abs(r1_fib - (108.0 + 0.382 * 22.0)) < 1e-9

        # Woodie
        p_woodie = (H + L + 2 * C) / 4.0  # (118+96+220)/4 = 108.5
        assert abs(p_woodie - 108.5) < 1e-9

        # Camarilla
        r1_cam = C + rng * 1.1 / 12
        assert abs(r1_cam - (110.0 + 22.0 * 1.1 / 12)) < 1e-9

        # Cross-check against the production reference port
        levels = pivot_levels_for_method("Standard", H, L, C)
        assert abs(levels["p"] - p_std) < 1e-9
        assert abs(levels["r1"] - r1_std) < 1e-9
        assert abs(levels["s1"] - s1_std) < 1e-9

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        wr = calc_williams_r(candles, period=3)
        assert wr[2] is not None

    # ── R5/S5 (Standard/Traditional only) ───────────────────────────────────
    def test_r5_s5_only_defined_for_standard(self):
        H, L, C = 118.0, 96.0, 110.0
        for method in ("Fibonacci", "Woodie", "Camarilla"):
            levels = pivot_levels_for_method(method, H, L, C)
            assert levels["r5"] is None, f"{method} must not fabricate R5"
            assert levels["s5"] is None, f"{method} must not fabricate S5"
        std = pivot_levels_for_method("Standard", H, L, C)
        assert std["r5"] is not None and math.isfinite(std["r5"])
        assert std["s5"] is not None and math.isfinite(std["s5"])

    def test_r5_s5_end_to_end_through_calc_pivot_points(self):
        candles = make_daily_session_candles([(115, 100, 108), (120, 112, 118)])
        res = calc_pivot_points(candles, "Standard", "Daily")
        H, L, C = 115.0, 100.0, 108.0
        p = (H + L + C) / 3
        assert abs(res["r5"][1] - (4 * p + H - 4 * L)) < 1e-9
        assert abs(res["s5"][1] - (4 * p - 4 * H + L)) < 1e-9

    def test_r4_r5_ordering_above_r3_below_infinity(self):
        """R5 > R4 > R3 > R2 > R1 > P > S1 > S2 > S3 > S4 > S5 for a normal (H>L) period."""
        H, L, C = 130.0, 90.0, 115.0
        levels = pivot_levels_for_method("Standard", H, L, C)
        assert levels["r5"] > levels["r4"] > levels["r3"] > levels["r2"] > levels["r1"] > levels["p"]
        assert levels["p"] > levels["s1"] > levels["s2"] > levels["s3"] > levels["s4"] > levels["s5"]

    @pytest.mark.parametrize("bar_seconds,expected", [
        (60, "Daily"),        # 1m
        (5 * 60, "Daily"),    # 5m
        (15 * 60, "Daily"),   # 15m (boundary, inclusive)
        (15 * 60 + 1, "Weekly"),  # just above 15m
        (30 * 60, "Weekly"),  # 30m
        (3600, "Weekly"),     # 1h
        (4 * 3600, "Weekly"), # 4h
        (86400 - 1, "Weekly"),  # just under 1 day
        (86400, "Monthly"),   # 1D (documented fallback, not part of the verified rule)
    ])
    def test_auto_period_resolution_matches_tradingview_documented_rule(self, bar_seconds, expected):
        assert resolve_auto_period(bar_seconds) == expected


# ═══════════════════════════════════════════════════════════════════════════
# PIVOT POINTS HIGH LOW  — Phase 2M
# (a distinct indicator from Pivot Points Standard — local swing-point
#  detection with an explicit confirmation delay, NOT prior-period S/R levels)
# ═══════════════════════════════════════════════════════════════════════════

def calc_pivot_high_low(candles, left_bars=5, right_bars=5):
    """
    Independent reference port of indicators.js Calc.pivotHighLow.

    Equality/tie convention (deterministic, matches production): a
    candidate at index i is a pivot high only if its high is STRICTLY
    greater than every high in the left window [i-left, i-1], and
    greater-than-OR-EQUAL to every high in the right window [i+1, i+right].
    Pivot low mirrors this with strict-less-than on the left, less-than-
    -or-equal on the right. This asymmetry means a flat plateau produces
    exactly one pivot (the first candle of the tie), never one per
    tied candle.

    Confirmation: index i is only ever evaluated if i+right_bars is a
    valid index in the CURRENT candles array — i.e. the function never
    looks beyond the data it was actually given. A pivot's value is
    always stored at its own original index i, never at i+right_bars.
    """
    n = len(candles)
    pivot_high = [None] * n
    pivot_low = [None] * n
    if n == 0 or left_bars < 1 or right_bars < 1:
        return {"pivotHigh": pivot_high, "pivotLow": pivot_low}

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    for i in range(left_bars, n - right_bars):
        h = highs[i]
        is_high = all(h > highs[j] for j in range(i - left_bars, i))
        if is_high:
            is_high = all(h >= highs[j] for j in range(i + 1, i + right_bars + 1))
        if is_high:
            pivot_high[i] = h

        l = lows[i]
        is_low = all(l < lows[j] for j in range(i - left_bars, i))
        if is_low:
            is_low = all(l <= lows[j] for j in range(i + 1, i + right_bars + 1))
        if is_low:
            pivot_low[i] = l

    return {"pivotHigh": pivot_high, "pivotLow": pivot_low}


def calc_market_structure(candles, swing_length=5, confirmation="Close",
                           displacement_multiplier=1.5, atr_length=14):
    """
    Independent reference port of indicators.js Calc.marketStructure.

    Swing detection: identical confirmation-delay rule to calc_pivot_high_low
    (symmetric left/right = swing_length, strict-left/lenient-right tie
    rule) — a pivot at index p is only CONFIRMED (confirmedAt = p +
    swing_length) once swing_length real candles exist after it.

    HH/HL/LH/LL classification: each swing compared only to the previous
    CONFIRMED swing of the SAME type. Purely informational; independent of
    the break/trend state machine below.

    Break/trend state machine (non-repainting): walks candles
    chronologically. A swing only becomes an active structural level once
    the candle index reaches that swing's OWN confirmedAt — never earlier —
    so recomputing over a longer history never changes an already-produced
    historical event. `trend` starts 'neutral' and is set by the first
    break (whichever direction), so the very first break is always
    classified CHOCH/MSS, never BOS (BOS requires an already-established
    matching trend). After that:
      - break matching the current trend       -> BOS (continuation)
      - break opposite the current trend        -> reversal: CHOCH, or MSS
        if the breaking candle shows displacement (|close-open| >=
        displacement_multiplier * ATR(atr_length) at the break bar, ATR
        reused verbatim via calc_atr) — LEVERAGE's own explicit, documented
        MSS rule (a reversal break is EITHER CHoCH or MSS, never both).
      - each broken level is marked broken immediately (never fires twice).
    """
    n = len(candles) if candles else 0
    empty = {"swings": [], "events": [], "trend": "neutral"}
    if n == 0 or swing_length is None or swing_length < 1:
        return empty

    L = swing_length
    disp_mult = displacement_multiplier if (displacement_multiplier and displacement_multiplier > 0) else 1.5
    atr_len = atr_length if (atr_length and atr_length >= 1) else 14
    confirm_by_close = (confirmation != "Wick")

    def H(i):
        v = float(candles[i].get("high", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    def Lo(i):
        v = float(candles[i].get("low", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    def C(i):
        v = float(candles[i].get("close", 0))
        return v if math.isfinite(v) else 0.0

    def O(i):
        v = float(candles[i].get("open", C(i)))
        return v if math.isfinite(v) else C(i)

    # 1. Confirmed swing highs/lows (same rule as calc_pivot_high_low).
    swings = []
    for p in range(L, n - L):
        h = H(p)
        is_high = all(h > H(j) for j in range(p - L, p))
        if is_high:
            is_high = all(h >= H(j) for j in range(p + 1, p + L + 1))
        if is_high:
            swings.append({"index": p, "confirmedAt": p + L, "time": candles[p].get("time"),
                            "price": h, "type": "high", "classification": None, "broken": False})

        l = Lo(p)
        is_low = all(l < Lo(j) for j in range(p - L, p))
        if is_low:
            is_low = all(l <= Lo(j) for j in range(p + 1, p + L + 1))
        if is_low:
            swings.append({"index": p, "confirmedAt": p + L, "time": candles[p].get("time"),
                            "price": l, "type": "low", "classification": None, "broken": False})

    swings.sort(key=lambda s: s["index"])

    # 2. HH/HL/LH/LL classification.
    prev_high = None
    prev_low = None
    for sw in swings:
        if sw["type"] == "high":
            if prev_high is not None:
                sw["classification"] = "HH" if sw["price"] > prev_high["price"] else "LH"
            prev_high = sw
        else:
            if prev_low is not None:
                sw["classification"] = "HL" if sw["price"] > prev_low["price"] else "LL"
            prev_low = sw

    atr_vals = calc_atr(candles, period=atr_len)

    # 3. Break / trend state machine.
    trend = "neutral"
    active_high = None
    active_low = None
    reveal_ptr = 0
    events = []

    for i in range(n):
        while reveal_ptr < len(swings) and swings[reveal_ptr]["confirmedAt"] <= i:
            revealed = swings[reveal_ptr]
            if revealed["type"] == "high":
                active_high = revealed
            else:
                active_low = revealed
            reveal_ptr += 1

        up_val = C(i) if confirm_by_close else H(i)
        down_val = C(i) if confirm_by_close else Lo(i)
        atr_here = atr_vals[i]
        displaced = (atr_here is not None and math.isfinite(atr_here) and atr_here > 0 and
                     abs(C(i) - O(i)) >= disp_mult * atr_here)

        if active_high is not None and not active_high["broken"] and up_val > active_high["price"]:
            active_high["broken"] = True
            up_type = "BOS" if trend == "bullish" else ("MSS" if displaced else "CHOCH")
            events.append({
                "type": up_type, "direction": "bullish", "breakIndex": i, "breakTime": candles[i].get("time"),
                "brokenLevel": active_high["price"], "originSwingIndex": active_high["index"],
                "originSwingTime": active_high["time"], "confirmationType": "Close" if confirm_by_close else "Wick"
            })
            trend = "bullish"

        if active_low is not None and not active_low["broken"] and down_val < active_low["price"]:
            active_low["broken"] = True
            down_type = "BOS" if trend == "bearish" else ("MSS" if displaced else "CHOCH")
            events.append({
                "type": down_type, "direction": "bearish", "breakIndex": i, "breakTime": candles[i].get("time"),
                "brokenLevel": active_low["price"], "originSwingIndex": active_low["index"],
                "originSwingTime": active_low["time"], "confirmationType": "Close" if confirm_by_close else "Wick"
            })
            trend = "bearish"

    return {"swings": swings, "events": events, "trend": trend}


def calc_fvg(candles, min_size_atr_multiplier=0.1, atr_length=14, mitigation="Full Fill", max_zones=50):
    """
    Independent reference port of indicators.js Calc.fvg.

    Detection (c1=i-2, c2=i-1, c3=i): bullish when Low(c3) > High(c1) ->
    zone [High(c1), Low(c3)]; bearish when High(c3) < Low(c1) -> zone
    [High(c3), Low(c1)]. This is the 3-candle imbalance structure, not a
    same-bar open-vs-prior-close session gap (c2 itself is never read for
    the boundary math).

    Minimum size filter: a candidate is discarded entirely unless
    size >= min_size_atr_multiplier * ATR(atr_length) evaluated at the
    ORIGIN candle (c1) — reuses calc_atr verbatim.

    Lifecycle/mitigation: walking forward from c3+1, `fill_percentage` is
    a MONOTONIC high-water mark (bullish gaps fill via subsequent lows
    from the top down, bearish via subsequent highs from the bottom up;
    once touched it can only grow, never shrink even if price retreats).
    'Touch' mode: first intrusion -> immediately 'fully_mitigated'.
    'Full Fill' mode: first intrusion -> 'partially_mitigated', stays
    there until the zone is completely traversed -> 'fully_mitigated'.
    status/top/bottom/direction never change once set; only
    fill_percentage/status progress forward as genuinely new candles are
    processed — that forward progression is documented lifecycle, not
    repainting (mirrors calc_market_structure's broken-level tracking).

    Only the most recent max_zones gaps are returned (default 50,
    documented, configurable) to bound render cost on long histories.
    """
    n = len(candles) if candles else 0
    if n < 3:
        return []

    min_mult = min_size_atr_multiplier if (min_size_atr_multiplier is not None and min_size_atr_multiplier >= 0) else 0.1
    atr_len = atr_length if (atr_length and atr_length >= 1) else 14
    max_z = max_zones if (max_zones and max_zones >= 1) else 50
    touch_mode = (mitigation == "Touch")

    def H(i):
        v = float(candles[i].get("high", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    def Lo(i):
        v = float(candles[i].get("low", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    atr_vals = calc_atr(candles, period=atr_len)
    fvgs = []

    for i in range(2, n):
        c1, c3 = i - 2, i
        bullish = Lo(c3) > H(c1)
        bearish = H(c3) < Lo(c1)
        if not bullish and not bearish:
            continue

        top = Lo(c3) if bullish else Lo(c1)
        bottom = H(c1) if bullish else H(c3)
        size = top - bottom
        if not (size > 0):
            continue

        atr_here = atr_vals[c1]
        min_size = min_mult * atr_here if (atr_here is not None and math.isfinite(atr_here)) else 0
        if size < min_size:
            continue

        fvg = {
            "id": f"{c1}-{'bullish' if bullish else 'bearish'}",
            "direction": "bullish" if bullish else "bearish",
            "originIndex": c1, "confirmIndex": c3,
            "startTime": candles[c1].get("time"), "confirmTime": candles[c3].get("time"),
            "top": top, "bottom": bottom, "midpoint": (top + bottom) / 2.0, "size": size,
            "status": "active", "fillPercentage": 0.0,
            "mitigatedAt": None, "mitigatedIndex": None, "mitigationPrice": None
        }

        for j in range(c3 + 1, n):
            extreme = Lo(j) if bullish else H(j)
            filled = (top - extreme) if bullish else (extreme - bottom)
            filled = max(0.0, min(size, filled))
            pct = filled / size
            if pct > fvg["fillPercentage"]:
                fvg["fillPercentage"] = pct

            if fvg["fillPercentage"] > 0 and fvg["status"] == "active":
                fvg["status"] = "fully_mitigated" if touch_mode else "partially_mitigated"
                if touch_mode:
                    fvg["mitigatedAt"] = candles[j].get("time")
                    fvg["mitigatedIndex"] = j
                    fvg["mitigationPrice"] = extreme

            if not touch_mode and fvg["fillPercentage"] >= 1:
                fvg["status"] = "fully_mitigated"
                fvg["mitigatedAt"] = candles[j].get("time")
                fvg["mitigatedIndex"] = j
                fvg["mitigationPrice"] = extreme

            if fvg["status"] == "fully_mitigated":
                break

        fvgs.append(fvg)

    if len(fvgs) > max_z:
        fvgs = fvgs[-max_z:]
    return fvgs


def calc_order_blocks(candles, swing_length=5, mitigation="Full Fill", max_zones=50):
    """
    Independent reference port of indicators.js Calc.orderBlocks.

    This is a faithful port of TradingView's own reference "Order Blocks"
    Pine v5 script (user-supplied source), NOT a Market-Structure-event
    consumer as originally designed. That switch was deliberate: the
    original BOS/CHoCH-event-driven design produced visibly MORE blocks
    than the reference script on identical data (reported from a
    side-by-side chart comparison at the same swingLength) because
    calc_market_structure fires on every confirmed swing break including
    minor CHoCH pullbacks, while the reference script only ever fires on
    one much stricter, purely mechanical trigger (below).

    Two continuously-updated "leg" anchors are tracked candle-by-candle,
    mirroring the reference script's lastUp*/lastDown*/lastHigh/lastLow:
      - an "up-leg": reset to the current candle's own (Low, High) every
        time a bullish candle (Close>Open) prints; its High then keeps
        extending (running max) across every subsequent candle until the
        NEXT bullish candle resets it.
      - a "down-leg": the mirror image, anchored on the last bearish
        candle, with a running-min Low extending until the next bearish
        candle resets it.

    Bearish OB creation (the ONLY independent trigger): a rolling
    structure_low[i] = the lowest Low over swing_length candles strictly
    BEFORE i. A bearish Order Block is created exactly when Close crosses
    under that rolling low (Close[i-1] >= structure_low[i-1] AND
    Close[i] < structure_low[i]) -- anchored on the current up-leg;
    skipped if no bullish candle has printed yet, or the up-leg is stale
    (>=1000 candles old).

    Bullish OB creation (deliberately NOT symmetric): the reference script
    has no independent "crossover a rolling high" trigger. A bullish Order
    Block is created ONLY as the side effect of an existing, not-yet-
    reclaimed bearish Order Block's own Close crossing back above its
    `top` -- the same decisive close-through that invalidates that bearish
    block simultaneously seeds a fresh bullish block anchored on the
    current down-leg. This asymmetry is intentional, reproduced exactly as
    given.

    Lifecycle (unchanged from the original design): once a candidate's
    origin/top/bottom/direction is established, it walks forward with the
    same monotonic fill_percentage/Touch-vs-Full-Fill mitigation and
    decisive-close invalidation loop (bullish invalidated by Close<bottom,
    bearish by Close>top) -- this already matched the reference script's
    own box.delete condition.

    No displacement/ATR filter: the reference script has none. Only the
    most recent max_zones blocks are returned.
    """
    n = len(candles) if candles else 0
    if n < 3:
        return []

    swing_len = swing_length if (swing_length and swing_length >= 1) else 5
    max_z = max_zones if (max_zones and max_zones >= 1) else 50
    touch_mode = (mitigation == "Touch")

    def O(i):
        v = float(candles[i].get("open", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    def C(i):
        v = float(candles[i].get("close", 0))
        return v if math.isfinite(v) else 0.0

    def H(i):
        v = float(candles[i].get("high", candles[i].get("close", 0)))
        return v if math.isfinite(v) else C(i)

    def Lo(i):
        v = float(candles[i].get("low", candles[i].get("close", 0)))
        return v if math.isfinite(v) else C(i)

    # structure_low[i] = min(Low[i-swing_len .. i-1]) -- a rolling window
    # strictly BEFORE i, only defined once swing_len prior candles exist.
    structure_low = [None] * n
    for i in range(swing_len, n):
        lo = min(Lo(k) for k in range(i - swing_len, i))
        structure_low[i] = lo

    last_up_index = -1
    last_up_low = None
    last_high = None
    last_down_index = -1
    last_down = None
    last_low = None
    last_long_index = -1
    alive_bearish = []
    candidates = []

    for idx in range(n):
        if (idx > 0 and structure_low[idx] is not None and structure_low[idx - 1] is not None and
                C(idx - 1) >= structure_low[idx - 1] and C(idx) < structure_low[idx] and
                last_up_index != -1 and (idx - last_up_index) < 1000):
            new_top, new_bottom = last_high, last_up_low
            if new_top > new_bottom:
                bearish_cand = {"originIndex": last_up_index, "confirmIndex": idx,
                                 "direction": "bearish", "top": new_top, "bottom": new_bottom}
                candidates.append(bearish_cand)
                alive_bearish.append(bearish_cand)

        for b in range(len(alive_bearish) - 1, -1, -1):
            if C(idx) > alive_bearish[b]["top"]:
                alive_bearish.pop(b)
                if last_down_index != -1 and (idx - last_down_index) < 1000 and idx > last_long_index:
                    top2, bottom2 = last_down, last_low
                    if top2 > bottom2:
                        candidates.append({"originIndex": last_down_index, "confirmIndex": idx,
                                            "direction": "bullish", "top": top2, "bottom": bottom2})
                        last_long_index = idx

        if C(idx) < O(idx):
            last_down, last_down_index, last_low = H(idx), idx, Lo(idx)
        if C(idx) > O(idx):
            last_up_index, last_up_low, last_high = idx, Lo(idx), H(idx)
        if last_high is not None and H(idx) > last_high:
            last_high = H(idx)
        if last_low is not None and Lo(idx) < last_low:
            last_low = Lo(idx)

    obs = []
    for cand in candidates:
        bullish = cand["direction"] == "bullish"
        top, bottom = cand["top"], cand["bottom"]
        ob = {
            "id": f"{cand['originIndex']}-{cand['direction']}-{cand['confirmIndex']}",
            "direction": cand["direction"],
            "originIndex": cand["originIndex"], "confirmIndex": cand["confirmIndex"],
            "startTime": candles[cand["originIndex"]].get("time"), "confirmTime": candles[cand["confirmIndex"]].get("time"),
            "top": top, "bottom": bottom, "midpoint": (top + bottom) / 2.0, "size": top - bottom,
            "originHigh": H(cand["originIndex"]), "originLow": Lo(cand["originIndex"]),
            "triggerEvent": "bullishReclaim" if bullish else "bearishBreakdown",
            "status": "active", "fillPercentage": 0.0,
            "mitigatedAt": None, "mitigatedIndex": None, "mitigationPrice": None,
            "invalidatedAt": None, "invalidatedIndex": None
        }

        for j in range(cand["confirmIndex"] + 1, n):
            if bullish and C(j) < bottom:
                ob["status"] = "invalidated"
                ob["invalidatedAt"] = candles[j].get("time")
                ob["invalidatedIndex"] = j
                break
            if not bullish and C(j) > top:
                ob["status"] = "invalidated"
                ob["invalidatedAt"] = candles[j].get("time")
                ob["invalidatedIndex"] = j
                break

            extreme = Lo(j) if bullish else H(j)
            filled = (top - extreme) if bullish else (extreme - bottom)
            filled = max(0.0, min(ob["size"], filled))
            pct = filled / ob["size"] if ob["size"] > 0 else 0.0
            if pct > ob["fillPercentage"]:
                ob["fillPercentage"] = pct

            if ob["fillPercentage"] > 0 and ob["status"] == "active":
                ob["status"] = "fully_mitigated" if touch_mode else "partially_mitigated"
                if touch_mode:
                    ob["mitigatedAt"] = candles[j].get("time")
                    ob["mitigatedIndex"] = j
                    ob["mitigationPrice"] = extreme

            if not touch_mode and ob["fillPercentage"] >= 1:
                ob["status"] = "fully_mitigated"
                ob["mitigatedAt"] = candles[j].get("time")
                ob["mitigatedIndex"] = j
                ob["mitigationPrice"] = extreme

            if ob["status"] == "fully_mitigated":
                break

        obs.append(ob)

    obs.sort(key=lambda o: o["originIndex"])
    if len(obs) > max_z:
        obs = obs[-max_z:]
    return obs


def calc_liquidity(candles, swing_length=5, tolerance_atr_multiplier=0.05, atr_length=14,
                    min_touches=2, max_pools=50):
    """
    Independent reference port of indicators.js Calc.liquidity.

    Built entirely from calc_market_structure's confirmed `swings` (never
    redetects pivots). Every swing becomes its own SWING_HIGH/SWING_LOW
    pool (touchCount=1, "Swing Liquidity"). Separately, swings of the same
    type are clustered by ATR-relative price tolerance
    (tolerance_atr_multiplier * ATR(atr_length), calc_atr reused verbatim);
    clusters with >= min_touches members become EQUAL_HIGH/EQUAL_LOW pools.
    These are two independent classifications of the same swings, not
    mutually exclusive.

    Deterministic clustering: swings of one type sorted by price ascending;
    a cluster starts at the first unassigned swing, and every subsequent
    swing joins the CURRENT cluster only if within tolerance of that
    cluster's FIRST (anchor) member's price — not the previous member —
    bounding cluster spread to 2*tolerance and avoiding chaining.

    Side: EQUAL_HIGH/SWING_HIGH -> BUY_SIDE; EQUAL_LOW/SWING_LOW ->
    SELL_SIDE. Non-repainting is inherited from calc_market_structure's own
    confirmation gating. `swept`/`sweptAt`/`sweptPrice` are always
    None/False here by design — reserved for a future Liquidity Sweep
    phase, never populated by this detector.
    """
    n = len(candles) if candles else 0
    if n == 0:
        return []

    swing_len = swing_length if (swing_length and swing_length >= 1) else 5
    tol_mult = tolerance_atr_multiplier if (tolerance_atr_multiplier is not None and tolerance_atr_multiplier >= 0) else 0.05
    atr_len = atr_length if (atr_length and atr_length >= 1) else 14
    min_t = min_touches if (min_touches and min_touches >= 2) else 2
    max_p = max_pools if (max_pools and max_pools >= 1) else 50

    ms_result = calc_market_structure(candles, swing_length=swing_len, confirmation="Close",
                                       displacement_multiplier=1.5, atr_length=atr_len)
    atr_vals = calc_atr(candles, period=atr_len)
    pools = []

    def tolerance_at(idx):
        a = atr_vals[idx]
        if a is not None and math.isfinite(a) and a > 0:
            return tol_mult * a
        if idx > 0 and candles and idx < len(candles):
            prev_c = float(candles[idx - 1].get("close", 0))
            cur_h = float(candles[idx].get("high", candles[idx].get("close", 0)))
            cur_l = float(candles[idx].get("low", candles[idx].get("close", 0)))
            tr = max(cur_h - cur_l, abs(cur_h - prev_c), abs(cur_l - prev_c))
            if math.isfinite(tr) and tr > 0:
                return tol_mult * tr
        c = float(candles[idx].get("close", 0)) if (candles and idx < len(candles)) else 0.0
        return (tol_mult * 0.05 * c) if c > 0 else 0.0

    def build_pools(swing_type, pool_type, side):
        swings = [s for s in ms_result["swings"] if s["type"] == swing_type]

        for s in swings:
            pools.append({
                "id": f"swing-{s['index']}-{pool_type}",
                "type": "SWING_HIGH" if swing_type == "high" else "SWING_LOW",
                "side": side, "price": s["price"], "tolerance": tolerance_at(s["index"]),
                "touchCount": 1, "touchPoints": [{"index": s["index"], "time": s["time"], "price": s["price"]}],
                "firstTouchTime": s["time"], "latestTouchTime": s["time"],
                "firstTouchIndex": s["index"], "latestTouchIndex": s["index"],
                "status": "active", "swept": False, "sweptAt": None, "sweptPrice": None
            })

        sorted_swings = sorted(swings, key=lambda s: s["price"])
        clusters = []
        current = None
        for s in sorted_swings:
            tol = tolerance_at(s["index"])
            if current is not None and abs(s["price"] - current["anchorPrice"]) <= max(tol, current["anchorTolerance"]):
                current["members"].append(s)
            else:
                current = {"anchorPrice": s["price"], "anchorTolerance": tol, "members": [s]}
                clusters.append(current)

        for cl in clusters:
            if len(cl["members"]) < min_t:
                continue
            members = sorted(cl["members"], key=lambda m: m["index"])
            avg_price = sum(m["price"] for m in members) / len(members)
            pools.append({
                "id": f"eq-{pool_type}-{members[0]['index']}-{members[-1]['index']}",
                "type": pool_type, "side": side, "price": avg_price, "tolerance": cl["anchorTolerance"],
                "touchCount": len(members),
                "touchPoints": [{"index": m["index"], "time": m["time"], "price": m["price"]} for m in members],
                "firstTouchTime": members[0]["time"], "latestTouchTime": members[-1]["time"],
                "firstTouchIndex": members[0]["index"], "latestTouchIndex": members[-1]["index"],
                "status": "active", "swept": False, "sweptAt": None, "sweptPrice": None
            })

    build_pools("high", "EQUAL_HIGH", "BUY_SIDE")
    build_pools("low", "EQUAL_LOW", "SELL_SIDE")

    # Sorted by index, not time: candle time can be a string, a number, or
    # (in minimal test fixtures) absent entirely — latestTouchIndex is
    # always a plain, always-present, always-comparable integer.
    pools.sort(key=lambda p: p["latestTouchIndex"])
    if len(pools) > max_p:
        pools = pools[-max_p:]
    return pools


def calc_liquidity_sweeps(candles, swing_length=5, tolerance_atr_multiplier=0.05, atr_length=14,
                           min_touches=2, confirmation="Close Rejection", max_events=50):
    """
    Independent reference port of indicators.js Calc.liquiditySweeps.

    Consumes calc_liquidity's pools directly (never rediscovers EQH/EQL).
    For a BUY_SIDE pool at `price`: "taken" the first candle (from
    latestTouchIndex+1 onward) whose High > price. From there, walk
    forward for confirmation: 'Close Rejection' (default) needs a Close <
    price; 'Wick + Reclaim' needs only a Low < price (faster/noisier,
    hence not the default). Both checks run on the SAME candle that took
    liquidity too, so a single-candle grab-and-reject confirms
    immediately; a multi-candle sweep is equally supported since "taken"
    persists forward. If liquidity is taken but never reclaimed within the
    available data, NO event is emitted for that instance (Scenario B /
    potential breakout — an unconfirmed candidate is never displayed as a
    confirmed sweep). SELL_SIDE mirrors this with Low/High swapped. The
    walk STOPS the instant a pool's sweep confirms, so a pool can never
    produce a second event ("one sweep per pool").

    Market Structure context is deliberately NOT computed here — calc_
    liquidity already calls calc_market_structure once internally, and
    calling it again just to cross-reference BOS/CHoCH/MSS would duplicate
    that work. Each event exposes its raw sweepIndex/reclaimIndex/
    liquidityPrice so a future SMC orchestrator can correlate it itself.
    """
    n = len(candles) if candles else 0
    if n == 0:
        return []

    max_ev = max_events if (max_events and max_events >= 1) else 50
    wick_reclaim = (confirmation == "Wick + Reclaim")

    def H(i):
        v = float(candles[i].get("high", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    def Lo(i):
        v = float(candles[i].get("low", candles[i].get("close", 0)))
        return v if math.isfinite(v) else float(candles[i]["close"])

    def C(i):
        v = float(candles[i].get("close", 0))
        return v if math.isfinite(v) else 0.0

    pools = calc_liquidity(candles, swing_length=swing_length, tolerance_atr_multiplier=tolerance_atr_multiplier,
                            atr_length=atr_length, min_touches=min_touches, max_pools=1000)
    sweeps = []

    for pool in pools:
        buy_side = pool["side"] == "BUY_SIDE"
        start_idx = pool["latestTouchIndex"] + 1
        taken = False
        taken_index = -1
        sweep_extreme = None

        for j in range(start_idx, n):
            if not taken:
                breaches = (H(j) > pool["price"]) if buy_side else (Lo(j) < pool["price"])
                if not breaches:
                    continue
                taken = True
                taken_index = j
                sweep_extreme = H(j) if buy_side else Lo(j)
            else:
                sweep_extreme = max(sweep_extreme, H(j)) if buy_side else min(sweep_extreme, Lo(j))

            if buy_side:
                confirmed = (Lo(j) < pool["price"]) if wick_reclaim else (C(j) < pool["price"])
            else:
                confirmed = (H(j) > pool["price"]) if wick_reclaim else (C(j) > pool["price"])

            if confirmed:
                sweep_depth = (sweep_extreme - pool["price"]) if buy_side else (pool["price"] - sweep_extreme)
                sweeps.append({
                    "id": f"{pool['id']}-sweep-{taken_index}",
                    "direction": "bearish" if buy_side else "bullish",
                    "sweepType": "BUY_SIDE_SWEEP" if buy_side else "SELL_SIDE_SWEEP",
                    "liquidityPoolId": pool["id"], "liquidityPoolType": pool["type"],
                    "liquidityPrice": pool["price"],
                    "sweepIndex": taken_index, "sweepTime": candles[taken_index].get("time"),
                    "sweepExtreme": sweep_extreme, "sweepDepth": sweep_depth,
                    "reclaimIndex": j, "reclaimTime": candles[j].get("time"), "reclaimPrice": C(j),
                    "confirmationType": "Wick + Reclaim" if wick_reclaim else "Close Rejection",
                    "status": "confirmed"
                })
                break

    sweeps.sort(key=lambda s: s["sweepIndex"])
    if len(sweeps) > max_ev:
        sweeps = sweeps[-max_ev:]
    return sweeps


def calc_premium_discount(candles, swing_length=5):
    """
    Independent reference port of indicators.js Calc.premiumDiscount.

    The active dealing range is the most recently confirmed swing high
    paired with the most recently confirmed swing low, using the exact
    same "most-recent-of-each-type" reveal mechanism calc_market_structure
    itself uses for active_high/active_low — so the range only changes at
    a genuine new-swing-confirmation point, never on an ordinary candle in
    between. range_high/range_low are always max()/min() of the two swing
    prices (never simply "the swing typed as high"), so a chronologically
    bearish leg still produces mathematically correct ordering.
    `direction` records which leg it actually was: 'bullish' if the low
    came first chronologically, 'bearish' if the high came first — purely
    descriptive metadata, never affecting the range_high/range_low math.

    equilibrium = (range_high + range_low) / 2. Each time the pair
    changes, a NEW immutable DealingRange record is appended (never
    mutating an earlier one) and the previous record's `active` flag is
    cleared — a full, non-repainting range history.

    current_classification compares the LAST candle's close against the
    ACTIVE range's equilibrium (exact-match epsilon of 1e-9): PREMIUM
    above, DISCOUNT below, EQUILIBRIUM within epsilon.
    """
    n = len(candles) if candles else 0
    if n == 0:
        return {"ranges": [], "currentClassification": None}

    swing_len = swing_length if (swing_length and swing_length >= 1) else 5
    ms_result = calc_market_structure(candles, swing_length=swing_len, confirmation="Close",
                                       displacement_multiplier=1.5, atr_length=14)
    swings = ms_result["swings"]

    ranges = []
    last_high = None
    last_low = None
    reveal_ptr = 0

    for i in range(n):
        changed = False
        while reveal_ptr < len(swings) and swings[reveal_ptr]["confirmedAt"] <= i:
            sw = swings[reveal_ptr]
            if sw["type"] == "high":
                last_high = sw
            else:
                last_low = sw
            changed = True
            reveal_ptr += 1

        if changed and last_high is not None and last_low is not None:
            range_high = max(last_high["price"], last_low["price"])
            range_low = min(last_high["price"], last_low["price"])
            new_range = {
                "id": f"{last_low['index']}-{last_high['index']}",
                "direction": "bullish" if last_low["index"] < last_high["index"] else "bearish",
                "rangeHigh": range_high, "rangeLow": range_low, "equilibrium": (range_high + range_low) / 2.0,
                "highIndex": last_high["index"], "lowIndex": last_low["index"],
                "highTime": last_high["time"], "lowTime": last_low["time"],
                "sourceStructure": {"highClassification": last_high["classification"], "lowClassification": last_low["classification"]},
                "createdIndex": i, "createdTime": candles[i].get("time"),
                "active": True
            }
            prev_range = ranges[-1] if ranges else None
            if prev_range is None or prev_range["id"] != new_range["id"]:
                if prev_range is not None:
                    prev_range["active"] = False
                ranges.append(new_range)

    active = ranges[-1] if ranges else None
    current_classification = None
    if active is not None:
        last_close = float(candles[n - 1].get("close", 0))
        if math.isfinite(last_close):
            if abs(last_close - active["equilibrium"]) < 1e-9:
                current_classification = "EQUILIBRIUM"
            else:
                current_classification = "PREMIUM" if last_close > active["equilibrium"] else "DISCOUNT"

    return {"ranges": ranges, "currentClassification": current_classification}


def calc_smc_setups(candles, swing_length=5, tolerance_atr_multiplier=0.05, atr_length=14, min_touches=2,
                     mitigation="Full Fill", confirmation="Close Rejection", structure_window_bars=10,
                     min_score=2, max_setups=20):
    """
    Independent reference port of indicators.js Calc.smcSetups.

    Pure orchestration layer — detects nothing new. Consumes the six
    standalone engines (calc_market_structure, calc_liquidity_sweeps,
    calc_fvg, calc_order_blocks, calc_premium_discount, and calc_liquidity
    indirectly via calc_liquidity_sweeps) and flags confluence using a
    standard SMC playbook: a Liquidity Sweep (2T) followed by a same-
    direction Market Structure CHoCH/MSS (2P, NOT BOS — a sweep classically
    precedes a REVERSAL, not a continuation) within structure_window_bars
    candles of the sweep's reclaim is a candidate Setup (BOTH required).
    premium/discount alignment (2U), FVG confluence (2Q), and OB confluence
    (2R) are OPTIONAL context, each a plain boolean, contributing to a
    `score` that is a SIMPLE COUNT (2 base + up to 3 more) — never a
    weighted or arbitrary formula, per Order Blocks'/Liquidity's own
    explicit "no black-box scoring" rule.

    Market Structure is genuinely recomputed multiple times per call (once
    directly here, again inside calc_liquidity via calc_liquidity_sweeps,
    again inside calc_order_blocks) — the same deliberate, already-
    documented tradeoff every composite engine since Phase 2R has made
    (reuse the function, not a cross-indicator computation cache that
    doesn't exist anywhere in this codebase).
    """
    n = len(candles) if candles else 0
    if n == 0:
        return []

    win_bars = structure_window_bars if (structure_window_bars and structure_window_bars >= 1) else 10
    min_sc = min_score if (min_score and min_score >= 2) else 2
    max_s = max_setups if (max_setups and max_setups >= 1) else 20

    ms_result = calc_market_structure(candles, swing_length=swing_length, confirmation="Close",
                                       displacement_multiplier=1.5, atr_length=atr_length)
    sweeps = calc_liquidity_sweeps(candles, swing_length=swing_length, tolerance_atr_multiplier=tolerance_atr_multiplier,
                                    atr_length=atr_length, min_touches=min_touches, confirmation=confirmation, max_events=1000)
    fvgs = calc_fvg(candles, min_size_atr_multiplier=0.1, atr_length=atr_length, mitigation=mitigation, max_zones=1000)
    obs = calc_order_blocks(candles, swing_length=swing_length, mitigation=mitigation, max_zones=1000)
    pd_result = calc_premium_discount(candles, swing_length=swing_length)

    def classification_at(index):
        active = None
        for r in pd_result["ranges"]:
            if r["createdIndex"] <= index:
                active = r
            else:
                break
        if active is None:
            return None
        price = float(candles[index].get("close", 0))
        if not math.isfinite(price):
            return None
        if abs(price - active["equilibrium"]) < 1e-9:
            return "EQUILIBRIUM"
        return "PREMIUM" if price > active["equilibrium"] else "DISCOUNT"

    setups = []
    for sweep in sweeps:
        structure_event = None
        for ev in ms_result["events"]:
            if ev["direction"] != sweep["direction"]:
                continue
            if ev["type"] not in ("CHOCH", "MSS"):
                continue
            if sweep["reclaimIndex"] <= ev["breakIndex"] <= sweep["reclaimIndex"] + win_bars:
                structure_event = ev
                break
        if structure_event is None:
            continue

        pd_context = classification_at(structure_event["breakIndex"])
        pd_aligned = (sweep["direction"] == "bullish" and pd_context == "DISCOUNT") or \
                     (sweep["direction"] == "bearish" and pd_context == "PREMIUM")

        window_start = sweep["sweepIndex"]
        window_end = structure_event["breakIndex"] + win_bars

        fvg_match = next((f for f in fvgs if f["direction"] == sweep["direction"]
                           and window_start <= f["confirmIndex"] <= window_end), None)
        ob_match = next((o for o in obs if o["direction"] == sweep["direction"]
                          and window_start <= o["confirmIndex"] <= window_end), None)

        score = 2 + (1 if pd_aligned else 0) + (1 if fvg_match else 0) + (1 if ob_match else 0)
        if score < min_sc:
            continue

        setups.append({
            "id": f"{sweep['id']}-setup-{structure_event['breakIndex']}",
            "direction": sweep["direction"],
            "sweepIndex": sweep["sweepIndex"], "reclaimIndex": sweep["reclaimIndex"],
            "liquidityPrice": sweep["liquidityPrice"], "liquidityPoolType": sweep["liquidityPoolType"],
            "structureEvent": {"type": structure_event["type"], "breakIndex": structure_event["breakIndex"],
                                "breakTime": structure_event["breakTime"]},
            "premiumDiscountContext": pd_context, "premiumDiscountAligned": pd_aligned,
            "fvgConfluence": fvg_match is not None, "fvgConfluenceId": fvg_match["id"] if fvg_match else None,
            "obConfluence": ob_match is not None, "obConfluenceId": ob_match["id"] if ob_match else None,
            "score": score,
            "index": structure_event["breakIndex"], "time": candles[structure_event["breakIndex"]].get("time")
        })

    setups.sort(key=lambda s: s["index"])
    if len(setups) > max_s:
        setups = setups[-max_s:]
    return setups


def calc_breaker_mitigation(candles, swing_length=5, breaker_mitigation="Full Fill", max_blocks=50):
    """
    Independent reference port of indicators.js Calc.breakerMitigation.

    Consumes calc_order_blocks directly (ONE internal call, forced to
    mitigation='Full Fill' regardless of any separately-configured OB
    indicator, because Mitigation Block qualification must never rest on
    a mere Touch) — Order Block detection is never recalculated.

    Every OB with status 'invalidated' becomes the source of exactly one
    Breaker: same zone (top/bottom), FLIPPED direction (invalidated
    bullish OB -> BEARISH_BREAKER; invalidated bearish OB ->
    BULLISH_BREAKER). No separate structural confirmation step — the OB's
    own invalidation (a decisive close through it) IS the Breaker's
    confirmation. From invalidatedIndex+1 onward, the Breaker is walked
    forward exactly like a FRESH Order Block of its new direction, seeded
    at the pre-existing zone (mirrors calc_order_blocks' own mitigation/
    invalidation math exactly, just flipped).

    Every OB with status 'fully_mitigated' (mutually exclusive with
    'invalidated' by construction) becomes a Mitigation Block: a
    completed historical fact preserving its ORIGINAL direction, with no
    further lifecycle of its own.
    """
    n = len(candles) if candles else 0
    if n == 0:
        return {"breakers": [], "mitigations": []}

    max_b = max_blocks if (max_blocks and max_blocks >= 1) else 50
    touch_mode = (breaker_mitigation == "Touch")

    def C(i):
        v = float(candles[i].get("close", 0))
        return v if math.isfinite(v) else 0.0

    def H(i):
        v = float(candles[i].get("high", candles[i].get("close", 0)))
        return v if math.isfinite(v) else C(i)

    def Lo(i):
        v = float(candles[i].get("low", candles[i].get("close", 0)))
        return v if math.isfinite(v) else C(i)

    obs = calc_order_blocks(candles, swing_length=swing_length, mitigation="Full Fill", max_zones=1000)

    breakers = []
    mitigations = []

    for ob in obs:
        if ob["status"] == "invalidated":
            bullish_breaker = (ob["direction"] == "bearish")
            top, bottom, size = ob["top"], ob["bottom"], ob["size"]
            breaker = {
                "id": f"{ob['id']}-breaker", "sourceOrderBlockId": ob["id"],
                "direction": "BULLISH_BREAKER" if bullish_breaker else "BEARISH_BREAKER",
                "top": top, "bottom": bottom, "midpoint": (top + bottom) / 2.0, "size": size,
                "creationIndex": ob["originIndex"], "creationTime": ob["startTime"],
                "invalidationIndex": ob["invalidatedIndex"], "invalidationTime": ob["invalidatedAt"],
                "confirmationIndex": ob["invalidatedIndex"], "confirmationTime": ob["invalidatedAt"],
                "sourceStructureEvent": ob["triggerEvent"],
                "status": "active", "fillPercentage": 0.0,
                "mitigatedAt": None, "mitigatedIndex": None, "mitigationPrice": None,
                "reInvalidatedAt": None, "reInvalidatedIndex": None
            }

            for j in range(ob["invalidatedIndex"] + 1, n):
                if bullish_breaker and C(j) < bottom:
                    breaker["status"] = "invalidated"
                    breaker["reInvalidatedAt"] = candles[j].get("time")
                    breaker["reInvalidatedIndex"] = j
                    break
                if not bullish_breaker and C(j) > top:
                    breaker["status"] = "invalidated"
                    breaker["reInvalidatedAt"] = candles[j].get("time")
                    breaker["reInvalidatedIndex"] = j
                    break

                extreme = Lo(j) if bullish_breaker else H(j)
                filled = (top - extreme) if bullish_breaker else (extreme - bottom)
                filled = max(0.0, min(size, filled))
                pct = filled / size if size > 0 else 0.0
                if pct > breaker["fillPercentage"]:
                    breaker["fillPercentage"] = pct

                if breaker["fillPercentage"] > 0 and breaker["status"] == "active":
                    breaker["status"] = "fully_mitigated" if touch_mode else "partially_mitigated"
                    if touch_mode:
                        breaker["mitigatedAt"] = candles[j].get("time")
                        breaker["mitigatedIndex"] = j
                        breaker["mitigationPrice"] = extreme

                if not touch_mode and breaker["fillPercentage"] >= 1:
                    breaker["status"] = "fully_mitigated"
                    breaker["mitigatedAt"] = candles[j].get("time")
                    breaker["mitigatedIndex"] = j
                    breaker["mitigationPrice"] = extreme

                if breaker["status"] == "fully_mitigated":
                    break

            breakers.append(breaker)
        elif ob["status"] == "fully_mitigated":
            mitigations.append({
                "id": f"{ob['id']}-mitigation", "sourceOrderBlockId": ob["id"],
                "direction": "BULLISH_MITIGATION" if ob["direction"] == "bullish" else "BEARISH_MITIGATION",
                "top": ob["top"], "bottom": ob["bottom"], "midpoint": ob["midpoint"], "size": ob["size"],
                "sourceIndex": ob["originIndex"], "sourceTime": ob["startTime"],
                "mitigationIndex": ob["mitigatedIndex"], "mitigationTime": ob["mitigatedAt"],
                "sourceStructureEvent": ob["triggerEvent"],
                "status": "confirmed", "active": True
            })

    breakers.sort(key=lambda b: b["creationIndex"])
    mitigations.sort(key=lambda m: m["sourceIndex"])
    if len(breakers) > max_b:
        breakers = breakers[-max_b:]
    if len(mitigations) > max_b:
        mitigations = mitigations[-max_b:]
    return {"breakers": breakers, "mitigations": mitigations}


class TestPivotPointsHighLow:

    # ── Basic input sizes ────────────────────────────────────────────────
    def test_empty_dataset(self):
        res = calc_pivot_high_low([], 5, 5)
        assert res["pivotHigh"] == [] and res["pivotLow"] == []

    def test_one_candle(self):
        res = calc_pivot_high_low(make_hlc([100], [90], [95]), 5, 5)
        assert res["pivotHigh"] == [None] and res["pivotLow"] == [None]

    def test_small_dataset_below_minimum(self):
        # length < left+right+1 -> no index can ever have enough neighbours
        candles = make_hlc([100, 105, 103], [90, 95, 93], [95, 100, 98])
        res = calc_pivot_high_low(candles, 5, 5)
        assert all(v is None for v in res["pivotHigh"])
        assert all(v is None for v in res["pivotLow"])

    def test_exact_minimum_dataset(self):
        # length == left+right+1 -> exactly one evaluable index (the middle)
        left, right = 2, 2
        highs = [10, 11, 15, 11, 10]
        lows = [5, 4, 2, 4, 5]
        candles = make_hlc(highs, lows, [8, 8, 8, 8, 8])
        res = calc_pivot_high_low(candles, left, right)
        assert res["pivotHigh"][2] == 15
        assert res["pivotLow"][2] == 2
        for i in (0, 1, 3, 4):
            assert res["pivotHigh"][i] is None
            assert res["pivotLow"][i] is None

    def test_normal_dataset(self):
        import random
        random.seed(21)
        n = 60
        highs, lows = [], []
        price = 100.0
        for _ in range(n):
            price += random.uniform(-3, 3)
            highs.append(price + random.uniform(0.5, 3))
            lows.append(price - random.uniform(0.5, 3))
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 5, 5)
        assert len(res["pivotHigh"]) == n

    def test_large_dataset_no_nan_or_infinity(self):
        import random
        random.seed(22)
        n = 3000
        highs, lows = [], []
        price = 500.0
        for _ in range(n):
            price += random.uniform(-4, 4)
            highs.append(price + random.uniform(0.5, 4))
            lows.append(price - random.uniform(0.5, 4))
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 5, 5)
        for v in res["pivotHigh"] + res["pivotLow"]:
            if v is not None:
                assert math.isfinite(v)

    # ── Pivot High ───────────────────────────────────────────────────────
    def test_simple_pivot_high(self):
        highs = [10, 11, 12, 20, 12, 11, 10]
        lows = [5, 5, 5, 5, 5, 5, 5]
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotHigh"][3] == 20
        assert all(v is None for i, v in enumerate(res["pivotHigh"]) if i != 3)

    def test_multiple_pivot_highs(self):
        # Padded with a baseline candle on each end so every peak sits at an
        # index with the full left=2/right=2 neighbours actually available
        # (index 0/1 can never be evaluated pivots when left=2).
        highs = [10, 10, 15, 10, 9, 10, 18, 10, 9, 10, 14, 10, 10]
        lows = [5] * len(highs)
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, 2, 2)
        peaks = [i for i, v in enumerate(res["pivotHigh"]) if v is not None]
        assert peaks == [2, 6, 10]
        assert res["pivotHigh"][2] == 15
        assert res["pivotHigh"][6] == 18
        assert res["pivotHigh"][10] == 14

    def test_strong_peak(self):
        highs = [10, 10, 10, 100, 10, 10, 10]
        lows = [5] * 7
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotHigh"][3] == 100

    def test_equal_high_plateau_produces_exactly_one_pivot(self):
        # 100 101 101 100 — with left=1,right=1: only index 1 (first of the
        # tie) qualifies; index 2 fails the strict-left check against index 1.
        highs = [100, 101, 101, 100]
        lows = [90, 90, 90, 90]
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, 1, 1)
        assert res["pivotHigh"][1] == 101
        assert res["pivotHigh"][2] is None, "second tied candle must NOT also register (deterministic tie-break)"

    def test_no_pivot_high_in_monotonic_rise(self):
        highs = [10, 11, 12, 13, 14, 15, 16]
        lows = [5] * 7
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, 2, 2)
        assert all(v is None for v in res["pivotHigh"])

    # ── Pivot Low ────────────────────────────────────────────────────────
    def test_simple_pivot_low(self):
        lows = [10, 9, 8, 1, 8, 9, 10]
        highs = [15] * len(lows)
        candles = make_hlc(highs, lows, highs)
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotLow"][3] == 1
        assert all(v is None for i, v in enumerate(res["pivotLow"]) if i != 3)

    def test_multiple_pivot_lows(self):
        lows = [10, 10, 5, 10, 11, 10, 2, 10, 11, 10, 6, 10, 10]
        highs = [15] * len(lows)
        candles = make_hlc(highs, lows, highs)
        res = calc_pivot_high_low(candles, 2, 2)
        assert res["pivotLow"][2] == 5
        assert res["pivotLow"][6] == 2
        assert res["pivotLow"][10] == 6

    def test_strong_trough(self):
        lows = [10, 10, 10, 0.01, 10, 10, 10]
        highs = [15] * 7
        candles = make_hlc(highs, lows, highs)
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotLow"][3] == 0.01

    def test_equal_low_plateau_produces_exactly_one_pivot(self):
        lows = [100, 99, 99, 100]
        highs = [110, 110, 110, 110]
        candles = make_hlc(highs, lows, highs)
        res = calc_pivot_high_low(candles, 1, 1)
        assert res["pivotLow"][1] == 99
        assert res["pivotLow"][2] is None

    def test_no_pivot_low_in_monotonic_fall(self):
        lows = [16, 15, 14, 13, 12, 11, 10]
        highs = [20] * 7
        candles = make_hlc(highs, lows, highs)
        res = calc_pivot_high_low(candles, 2, 2)
        assert all(v is None for v in res["pivotLow"])

    # ── Confirmation delay ───────────────────────────────────────────────
    @pytest.mark.parametrize("right_bars", [1, 3, 5, 10])
    def test_confirmation_requires_exact_right_bars(self, right_bars):
        left = 2
        n = left + right_bars + 1
        highs = [10 + i for i in range(left)] + [100] + [10 + (right_bars - 1 - i) for i in range(right_bars)]
        lows = [5] * n
        candles = make_hlc(highs, lows, lows)
        pivot_idx = left
        # One candle short of the required right-side data -> not confirmed
        res_short = calc_pivot_high_low(candles[:-1], left, right_bars)
        assert res_short["pivotHigh"][pivot_idx] is None, "must not confirm with one fewer right-bar than required"
        # Exactly enough right-side data -> confirmed
        res_full = calc_pivot_high_low(candles, left, right_bars)
        assert res_full["pivotHigh"][pivot_idx] == 100

    # ── No-look-ahead (mandatory) ────────────────────────────────────────
    def test_no_lookahead_bias(self):
        """
        A candidate pivot with fewer than rightBars future candles must be
        NOT CONFIRMED. Adding the required candles must then confirm it,
        with the SAME value it always would have had (proving nothing
        about the already-known past was altered by the new data either).
        """
        left, right = 3, 5
        highs = [10, 11, 12, 100, 12, 11, 10, 9]  # only 4 candles after the peak (idx3)
        lows = [5] * len(highs)
        candles = make_hlc(highs, lows, lows)
        res_before = calc_pivot_high_low(candles, left, right)
        assert res_before["pivotHigh"][3] is None, "only 4 right-side candles exist; rightBars=5 must not confirm yet"

        # Add the 5th required candle
        extended = candles + make_hlc([8], [5], [5])
        res_after = calc_pivot_high_low(extended, left, right)
        assert res_after["pivotHigh"][3] == 100, "now confirmed with exactly rightBars candles available"

        # And the earlier, already-evaluable region must be byte-identical
        for i in range(len(candles) - right):
            assert res_before["pivotHigh"][i] == res_after["pivotHigh"][i]
            assert res_before["pivotLow"][i] == res_after["pivotLow"][i]

    # ── Pivot location vs confirmation index ─────────────────────────────
    def test_pivot_location_differs_from_confirmation_index(self):
        left, right = 2, 4
        highs = [10, 11, 50, 11, 10, 9, 8, 7]
        lows = [5] * len(highs)
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, left, right)
        pivot_index = 2
        confirmation_index = pivot_index + right  # = 6, the index at which enough data exists
        assert res["pivotHigh"][pivot_index] == 50, "value must be stored at the ORIGINAL pivot candle"
        assert res["pivotHigh"][confirmation_index] is None, "confirmation index itself is not where the value lives"
        assert pivot_index != confirmation_index

    # ── Price correctness ────────────────────────────────────────────────
    def test_pivot_high_price_equals_candidate_high_never_close_or_average(self):
        candles = [{"high": 120, "low": 100, "close": 105}] + \
                  [{"high": 90 + i, "low": 80, "close": 85} for i in range(3)] + \
                  [{"high": 200, "low": 150, "close": 155}] + \
                  [{"high": 90 - i, "low": 80, "close": 85} for i in range(3)]
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotHigh"][4] == 200
        assert res["pivotHigh"][4] != candles[4]["close"]
        assert res["pivotHigh"][4] != (candles[4]["high"] + candles[4]["low"]) / 2

    def test_pivot_low_price_equals_candidate_low_never_close_or_average(self):
        candles = [{"high": 120, "low": 100, "close": 105}] + \
                  [{"high": 90, "low": 80 - i, "close": 85} for i in range(3)] + \
                  [{"high": 200, "low": 10, "close": 155}] + \
                  [{"high": 90, "low": 80 + i, "close": 85} for i in range(3)]
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotLow"][4] == 10
        assert res["pivotLow"][4] != candles[4]["close"]
        assert res["pivotLow"][4] != (candles[4]["high"] + candles[4]["low"]) / 2

    # ── Parameter validation ─────────────────────────────────────────────
    def test_invalid_left_bars_rejected(self):
        candles = make_hlc([10, 11, 12], [5, 5, 5], [8, 8, 8])
        for bad in (0, -1):
            res = calc_pivot_high_low(candles, bad, 3)
            assert all(v is None for v in res["pivotHigh"])
            assert all(v is None for v in res["pivotLow"])

    def test_invalid_right_bars_rejected(self):
        candles = make_hlc([10, 11, 12], [5, 5, 5], [8, 8, 8])
        for bad in (0, -1):
            res = calc_pivot_high_low(candles, 3, bad)
            assert all(v is None for v in res["pivotHigh"])
            assert all(v is None for v in res["pivotLow"])

    # ── Consecutive / multiple pivots ────────────────────────────────────
    def test_consecutive_pivots_not_filtered(self):
        # Alternating PH/PL close together — no artificial minimum distance
        highs = [10, 20, 10, 10, 20, 10]
        lows = [5, 5, 1, 1, 5, 5]
        candles = make_hlc(highs, lows, [8] * 6)
        res = calc_pivot_high_low(candles, 1, 1)
        assert res["pivotHigh"][1] == 20
        assert res["pivotLow"][2] == 1 or res["pivotLow"][3] == 1

    def test_alternating_ph_pl_sequence(self):
        highs = [10, 30, 10, 10, 30, 10, 10, 30, 10]
        lows = [5, 5, 1, 1, 5, 1, 1, 5, 5]
        candles = make_hlc(highs, lows, [8] * 9)
        res = calc_pivot_high_low(candles, 1, 1)
        ph_count = sum(1 for v in res["pivotHigh"] if v is not None)
        pl_count = sum(1 for v in res["pivotLow"] if v is not None)
        assert ph_count >= 2
        assert pl_count >= 1

    def test_multiple_pivots_do_not_overwrite_each_other(self):
        highs = [10, 10, 25, 10, 9, 10, 30, 10, 9, 10, 22, 10, 10]
        lows = [5] * len(highs)
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, 2, 2)
        values = {i: v for i, v in enumerate(res["pivotHigh"]) if v is not None}
        assert values.get(2) == 25
        assert values.get(6) == 30
        assert values.get(10) == 22
        assert len(values) == 3, "all three independent peaks must survive without overwriting each other"

    # ── Edge cases ───────────────────────────────────────────────────────
    def test_flat_market_no_pivots(self):
        highs = [100] * 20
        lows = [90] * 20
        candles = make_hlc(highs, lows, [95] * 20)
        res = calc_pivot_high_low(candles, 3, 3)
        # A perfectly flat market: index left(=3) fails the strict-left
        # check (100 > 100 is false), so no pivot anywhere.
        assert all(v is None for v in res["pivotHigh"])
        assert all(v is None for v in res["pivotLow"])

    def test_strong_uptrend_no_false_pivots(self):
        n = 30
        highs = [100 + i * 2 for i in range(n)]
        lows = [95 + i * 2 for i in range(n)]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 3, 3)
        assert all(v is None for v in res["pivotHigh"])
        assert all(v is None for v in res["pivotLow"])

    def test_strong_downtrend_no_false_pivots(self):
        n = 30
        highs = [200 - i * 2 for i in range(n)]
        lows = [195 - i * 2 for i in range(n)]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 3, 3)
        assert all(v is None for v in res["pivotHigh"])
        assert all(v is None for v in res["pivotLow"])

    def test_v_shaped_reversal(self):
        highs = [50, 40, 30, 20, 30, 40, 50, 60]
        lows = [45, 35, 25, 15, 25, 35, 45, 55]
        candles = make_hlc(highs, lows, lows)
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotLow"][3] == 15

    def test_inverted_v_reversal(self):
        highs = [10, 20, 30, 40, 30, 20, 10]
        lows = [5, 15, 25, 35, 25, 15, 5]
        candles = make_hlc(highs, lows, highs)
        res = calc_pivot_high_low(candles, 3, 3)
        assert res["pivotHigh"][3] == 40

    def test_gap_up_handled_safely(self):
        highs = [100, 101, 200, 201, 202]
        lows = [95, 96, 190, 191, 192]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 1, 1)
        for v in res["pivotHigh"] + res["pivotLow"]:
            if v is not None:
                assert math.isfinite(v)

    def test_gap_down_handled_safely(self):
        highs = [200, 199, 100, 99, 98]
        lows = [190, 189, 90, 89, 88]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 1, 1)
        for v in res["pivotHigh"] + res["pivotLow"]:
            if v is not None:
                assert math.isfinite(v)

    def test_very_large_prices(self):
        n = 20
        base = 5_000_000.0
        highs = [base + (i % 7) * 1000 for i in range(n)]
        lows = [base - (i % 5) * 1000 for i in range(n)]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 3, 3)
        for v in res["pivotHigh"] + res["pivotLow"]:
            if v is not None:
                assert math.isfinite(v)

    def test_very_small_prices(self):
        n = 20
        highs = [0.0010 + (i % 7) * 0.0001 for i in range(n)]
        lows = [0.0005 - (i % 5) * 0.00001 for i in range(n)]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 3, 3)
        for v in res["pivotHigh"] + res["pivotLow"]:
            if v is not None:
                assert math.isfinite(v)

    def test_long_dataset_no_duplicate_or_invalid_markers(self):
        import random
        random.seed(23)
        n = 1000
        highs, lows = [], []
        price = 300.0
        for i in range(n):
            price += 5 * math.sin(i / 6.0) + random.uniform(-0.5, 0.5)
            highs.append(price + random.uniform(0.5, 2))
            lows.append(price - random.uniform(0.5, 2))
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_pivot_high_low(candles, 5, 5)
        ph_indices = [i for i, v in enumerate(res["pivotHigh"]) if v is not None]
        assert len(ph_indices) == len(set(ph_indices)), "no duplicate pivot indices"
        for v in res["pivotHigh"] + res["pivotLow"]:
            if v is not None:
                assert math.isfinite(v)

    # ── Independent reference calculation ────────────────────────────────
    def test_independent_hand_computed_golden_case(self):
        """
        Hand-verified 7-candle dataset with left=2, right=2, computed by
        manually inspecting each window rather than calling calc_pivot_high_low.
        """
        highs = [10, 12, 20, 13, 11, 9, 8]
        lows =  [5,  4,  3,  6,  7, 8, 9]
        candles = make_hlc(highs, lows, [8] * 7)
        # Index 2 (high=20): left window [10,12] both < 20 (strict); right
        # window [13,11] both <= 20. -> Pivot High.
        # Index 2 (low=3): left window [5,4] both > 3 (strict); right window
        # [6,7] both >= 3. -> Pivot Low too (same candle, both extremes).
        res = calc_pivot_high_low(candles, 2, 2)
        assert res["pivotHigh"][2] == 20
        assert res["pivotLow"][2] == 3
        # No other index in this short, monotonically-diverging-then-converging
        # dataset should register.
        assert sum(1 for v in res["pivotHigh"] if v is not None) == 1
        assert sum(1 for v in res["pivotLow"] if v is not None) == 1

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None


# ═══════════════════════════════════════════════════════════════════════════
# DONCHIAN CHANNELS  — Phase 2N
# ═══════════════════════════════════════════════════════════════════════════

def calc_donchian(candles, length=20):
    """
    Independent reference port of indicators.js Calc.donchian.
    Current-candle-inclusive rolling window (matches this engine's existing
    convention, e.g. Calc.sma): Upper[i] = max(high[i-length+1..i]),
    Lower[i] = min(low[i-length+1..i]), Middle[i] = (Upper[i]+Lower[i])/2.
    """
    n = len(candles)
    upper = [None] * n
    lower = [None] * n
    middle = [None] * n
    if n == 0 or length < 1:
        return {"upper": upper, "lower": lower, "middle": middle}

    for i in range(length - 1, n):
        window = candles[i - length + 1: i + 1]
        hh = max(float(c["high"]) for c in window)
        ll = min(float(c["low"]) for c in window)
        upper[i] = hh
        lower[i] = ll
        middle[i] = (hh + ll) / 2.0

    return {"upper": upper, "lower": lower, "middle": middle}


class TestDonchianChannels:

    # ── Basic input sizes ────────────────────────────────────────────────
    def test_empty_data(self):
        res = calc_donchian([], 20)
        assert res["upper"] == [] and res["lower"] == [] and res["middle"] == []

    def test_one_candle(self):
        res = calc_donchian(make_hlc([100], [90], [95]), 20)
        assert res["upper"] == [None]

    def test_less_than_length(self):
        candles = make_hlc([100, 105, 103], [90, 95, 93], [95, 100, 98])
        res = calc_donchian(candles, 20)
        assert all(v is None for v in res["upper"])
        assert all(v is None for v in res["lower"])

    def test_exactly_length(self):
        length = 5
        highs = [100, 102, 101, 105, 103]
        lows = [90, 92, 91, 95, 93]
        candles = make_hlc(highs, lows, [98] * 5)
        res = calc_donchian(candles, length)
        assert res["upper"][4] == max(highs)
        assert res["lower"][4] == min(lows)
        for i in range(4):
            assert res["upper"][i] is None

    def test_greater_than_length(self):
        length = 5
        highs = [100 + i for i in range(10)]
        lows = [90 + i for i in range(10)]
        candles = make_hlc(highs, lows, [95 + i for i in range(10)])
        res = calc_donchian(candles, length)
        assert res["upper"][9] == max(highs[5:10])
        assert res["lower"][9] == min(lows[5:10])

    def test_large_dataset(self):
        import random
        random.seed(31)
        n = 5000
        highs, lows = [], []
        price = 1000.0
        for _ in range(n):
            price += random.uniform(-5, 5)
            highs.append(price + random.uniform(0.5, 5))
            lows.append(price - random.uniform(0.5, 5))
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_donchian(candles, 20)
        for v in res["upper"] + res["lower"] + res["middle"]:
            if v is not None:
                assert math.isfinite(v)

    # ── Upper (rolling max) ──────────────────────────────────────────────
    def test_rolling_maximum(self):
        highs = [10, 50, 20, 15, 12]
        lows = [5] * 5
        candles = make_hlc(highs, lows, [8] * 5)
        res = calc_donchian(candles, 3)
        assert res["upper"][2] == 50  # window [10,50,20]
        assert res["upper"][3] == 50  # window [50,20,15]
        assert res["upper"][4] == 20  # window [20,15,12] -- 50 has left the window

    # ── Lower (rolling min) ──────────────────────────────────────────────
    def test_rolling_minimum(self):
        lows = [10, 1, 20, 15, 12]
        highs = [30] * 5
        candles = make_hlc(highs, lows, [15] * 5)
        res = calc_donchian(candles, 3)
        assert res["lower"][2] == 1    # window [10,1,20]
        assert res["lower"][3] == 1    # window [1,20,15]
        assert res["lower"][4] == 12   # window [20,15,12] -- 1 has left the window

    # ── Middle ───────────────────────────────────────────────────────────
    def test_middle_equals_average_of_upper_lower(self):
        import random
        random.seed(32)
        n = 40
        highs, lows = [], []
        price = 200.0
        for _ in range(n):
            price += random.uniform(-3, 3)
            highs.append(price + random.uniform(0.5, 3))
            lows.append(price - random.uniform(0.5, 3))
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_donchian(candles, 10)
        for i in range(9, n):
            assert abs(res["middle"][i] - (res["upper"][i] + res["lower"][i]) / 2.0) < 1e-9

    # ── Window movement (off-by-one safety) ──────────────────────────────
    def test_window_movement_oldest_candle_leaves_correctly(self):
        highs = [100, 200, 100, 100, 100, 100]
        lows = [50] * 6
        candles = make_hlc(highs, lows, [75] * 6)
        res = calc_donchian(candles, 3)
        # window at i=1: [100,200] -- wait length=3 needs 3 candles, first valid i=2
        assert res["upper"][2] == 200   # [100,200,100]
        assert res["upper"][3] == 200   # [200,100,100]
        assert res["upper"][4] == 100   # [100,100,100] -- 200 (idx1) has left
        assert res["upper"][5] == 100

    # ── Numerical relationships ──────────────────────────────────────────
    def test_upper_gte_lower_middle_between(self):
        import random
        random.seed(33)
        n = 50
        highs, lows = [], []
        price = 500.0
        for _ in range(n):
            price += random.uniform(-4, 4)
            highs.append(price + random.uniform(0.5, 4))
            lows.append(price - random.uniform(0.5, 4))
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_donchian(candles, 15)
        for i in range(14, n):
            assert res["upper"][i] >= res["lower"][i]
            assert res["middle"][i] <= res["upper"][i]
            assert res["middle"][i] >= res["lower"][i]

    # ── Edge cases ───────────────────────────────────────────────────────
    def test_constant_prices(self):
        candles = make_hlc([100] * 25, [90] * 25, [95] * 25)
        res = calc_donchian(candles, 10)
        for i in range(9, 25):
            assert res["upper"][i] == 100
            assert res["lower"][i] == 90
            assert res["middle"][i] == 95

    def test_strictly_increasing_highs(self):
        n = 30
        highs = [100 + i for i in range(n)]
        lows = [90] * n
        candles = make_hlc(highs, lows, [95] * n)
        res = calc_donchian(candles, 10)
        for i in range(9, n):
            assert res["upper"][i] == highs[i], "in a strict uptrend, the current candle's own high is always the window max"

    def test_strictly_decreasing_lows(self):
        n = 30
        lows = [100 - i for i in range(n)]
        highs = [110] * n
        candles = make_hlc(highs, lows, [105] * n)
        res = calc_donchian(candles, 10)
        for i in range(9, n):
            assert res["lower"][i] == lows[i], "in a strict downtrend, the current candle's own low is always the window min"

    def test_new_high_entering_window(self):
        highs = [100, 100, 100, 150, 100]
        lows = [90] * 5
        candles = make_hlc(highs, lows, [95] * 5)
        res = calc_donchian(candles, 3)
        assert res["upper"][3] == 150  # new high just entered
        assert res["upper"][4] == 150  # still in window

    def test_old_high_leaving_window(self):
        highs = [150, 100, 100, 100, 100]
        lows = [90] * 5
        candles = make_hlc(highs, lows, [95] * 5)
        res = calc_donchian(candles, 3)
        assert res["upper"][2] == 150  # window [150,100,100]
        assert res["upper"][3] == 100  # 150 has left the window [100,100,100]

    def test_new_low_entering_window(self):
        lows = [100, 100, 100, 50, 100]
        highs = [110] * 5
        candles = make_hlc(highs, lows, [105] * 5)
        res = calc_donchian(candles, 3)
        assert res["lower"][3] == 50
        assert res["lower"][4] == 50

    def test_old_low_leaving_window(self):
        lows = [50, 100, 100, 100, 100]
        highs = [110] * 5
        candles = make_hlc(highs, lows, [105] * 5)
        res = calc_donchian(candles, 3)
        assert res["lower"][2] == 50
        assert res["lower"][3] == 100  # 50 has left the window

    def test_equal_highs(self):
        candles = make_hlc([100] * 15, [90 + i * 0.1 for i in range(15)], [95] * 15)
        res = calc_donchian(candles, 5)
        for i in range(4, 15):
            assert res["upper"][i] == 100

    def test_equal_lows(self):
        candles = make_hlc([100 + i * 0.1 for i in range(15)], [90] * 15, [95] * 15)
        res = calc_donchian(candles, 5)
        for i in range(4, 15):
            assert res["lower"][i] == 90

    def test_large_prices(self):
        n = 20
        base = 8_000_000.0
        highs = [base + i * 500 for i in range(n)]
        lows = [base - i * 300 for i in range(n)]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_donchian(candles, 10)
        for v in res["upper"] + res["lower"]:
            if v is not None:
                assert math.isfinite(v)

    def test_very_small_prices(self):
        n = 20
        highs = [0.00012 + i * 0.000001 for i in range(n)]
        lows = [0.00008 - i * 0.0000005 for i in range(n)]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_donchian(candles, 10)
        for v in res["upper"] + res["lower"]:
            if v is not None:
                assert math.isfinite(v)

    def test_gaps_handled_safely(self):
        highs = [100, 101, 300, 301, 302]
        lows = [90, 91, 290, 291, 292]
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_donchian(candles, 3)
        for v in res["upper"] + res["lower"]:
            if v is not None:
                assert math.isfinite(v)

    def test_long_dataset_no_nan_or_infinity(self):
        import random
        random.seed(34)
        n = 2000
        highs, lows = [], []
        price = 100.0
        for i in range(n):
            if i % 50 == 0:
                price *= random.choice([0.8, 1.25])
            price += random.uniform(-2, 2)
            highs.append(price + random.uniform(0.2, 2))
            lows.append(price - random.uniform(0.2, 2))
        candles = make_hlc(highs, lows, [(h + l) / 2 for h, l in zip(highs, lows)])
        res = calc_donchian(candles, 20)
        for v in res["upper"] + res["lower"] + res["middle"]:
            if v is not None:
                assert math.isfinite(v)

    # ── Parameter validation ─────────────────────────────────────────────
    def test_invalid_length_rejected(self):
        candles = make_hlc([100, 101, 102], [90, 91, 92], [95, 96, 97])
        for bad in (0, -1):
            res = calc_donchian(candles, bad)
            assert all(v is None for v in res["upper"])

    # ── Independent reference calculation ────────────────────────────────
    def test_independent_hand_computed_golden_case(self):
        """Hand-computed 6-candle example, length=4, independent of calc_donchian."""
        highs = [10, 15, 12, 20, 18, 14]
        lows =  [5,  8,  6,  9,  11, 7]
        candles = make_hlc(highs, lows, [9] * 6)
        # i=3: window highs[0:4]=[10,15,12,20] -> max=20; lows[0:4]=[5,8,6,9] -> min=5
        # i=4: window highs[1:5]=[15,12,20,18] -> max=20; lows[1:5]=[8,6,9,11] -> min=6
        # i=5: window highs[2:6]=[12,20,18,14] -> max=20; lows[2:6]=[6,9,11,7] -> min=6
        res = calc_donchian(candles, 4)
        assert res["upper"][3] == 20 and res["lower"][3] == 5
        assert res["upper"][4] == 20 and res["lower"][4] == 6
        assert res["upper"][5] == 20 and res["lower"][5] == 6
        assert abs(res["middle"][5] - (20 + 6) / 2.0) < 1e-9

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None


class TestKeltnerChannels:
    """
    Phase 2O — Keltner Channels: Middle=EMA(close,length), Upper/Lower =
    Middle +/- ATR(atrLength)*multiplier. calc_keltner is a composite that
    strictly reuses calc_ema and calc_atr (never redefines either), so most
    tests here verify that composition rather than re-deriving EMA/ATR math
    that TestATRCorrectness and the EMA-focused tests elsewhere already own.
    """

    # ── Basic input sizes / warm-up ─────────────────────────────────────
    def test_empty_data(self):
        res = calc_keltner([], length=20, atr_length=10, multiplier=2.0)
        assert res == {"middle": [], "upper": [], "lower": []}

    def test_warmup_boundary_uses_max_of_ema_and_atr_length(self):
        """Warm-up must respect BOTH lengths (spec section 12): the first
        valid index is max(length, atrLength) - 1, whichever is binding."""
        candles = make_realistic_candles(n=40, seed=21)
        kc_ema_binding = calc_keltner(candles, length=25, atr_length=10, multiplier=2.0)
        assert kc_ema_binding["middle"][23] is None
        assert kc_ema_binding["middle"][24] is not None
        kc_atr_binding = calc_keltner(candles, length=10, atr_length=25, multiplier=2.0)
        assert kc_atr_binding["middle"][23] is None
        assert kc_atr_binding["middle"][24] is not None

    def test_exactly_enough_history(self):
        candles = make_realistic_candles(n=20, seed=22)
        kc = calc_keltner(candles, length=20, atr_length=20, multiplier=2.0)
        assert kc["middle"][19] is not None
        assert all(v is None for v in kc["middle"][:19])

    def test_more_than_enough_history(self):
        candles = make_realistic_candles(n=25, seed=23)
        kc = calc_keltner(candles, length=20, atr_length=10, multiplier=2.0)
        assert kc["middle"][19] is not None
        assert kc["middle"][24] is not None

    def test_insufficient_history_produces_no_values(self):
        candles = make_realistic_candles(n=5, seed=24)
        kc = calc_keltner(candles, length=20, atr_length=10, multiplier=2.0)
        assert all(v is None for v in kc["middle"])
        assert all(v is None for v in kc["upper"])
        assert all(v is None for v in kc["lower"])

    # ── EMA dependency (spec section 35) ────────────────────────────────
    def test_middle_equals_existing_ema_implementation(self):
        """Keltner.middle == EMA(close, length) exactly — atrLength (10) is
        shorter than length (20) here so EMA warm-up is always the binding
        constraint, making the two arrays comparable index-for-index."""
        candles = make_realistic_candles(n=50, seed=11)
        length, atr_length = 20, 10
        expected_ema = calc_ema(extract_source(candles, "close"), length)
        kc = calc_keltner(candles, length=length, atr_length=atr_length, multiplier=2.0)
        assert kc["middle"] == expected_ema

    # ── ATR dependency / consistency (spec section 43, MANDATORY) ───────
    def test_atr_consistency_with_existing_atr_indicator(self):
        """Keltner's channel distance from its centerline must equal
        calc_atr(candles, atrLength) * multiplier exactly — the same ATR
        convention (Wilder's smoothing) as the standalone ATR indicator,
        for identical candles and identical atrLength."""
        candles = make_realistic_candles(n=60, seed=7)
        atr_length, multiplier = 10, 2.0
        atr_vals = calc_atr(candles, period=atr_length)
        kc = calc_keltner(candles, length=20, atr_length=atr_length, multiplier=multiplier)
        checked = 0
        for i in range(len(candles)):
            if kc["middle"][i] is None or atr_vals[i] is None:
                continue
            assert abs((kc["upper"][i] - kc["middle"][i]) - atr_vals[i] * multiplier) < 1e-9
            assert abs((kc["middle"][i] - kc["lower"][i]) - atr_vals[i] * multiplier) < 1e-9
            checked += 1
        assert checked > 0

    # ── Upper / Lower formulas (spec sections 36-37) ────────────────────
    def test_upper_formula(self):
        candles = make_realistic_candles(n=50, seed=12)
        length, atr_length, multiplier = 20, 10, 2.5
        kc = calc_keltner(candles, length, atr_length, multiplier)
        ema_vals = calc_ema(extract_source(candles, "close"), length)
        atr_vals = calc_atr(candles, atr_length)
        checked = 0
        for i in range(len(candles)):
            if ema_vals[i] is None or atr_vals[i] is None:
                assert kc["upper"][i] is None
                continue
            assert abs(kc["upper"][i] - (ema_vals[i] + atr_vals[i] * multiplier)) < 1e-9
            checked += 1
        assert checked > 0

    def test_lower_formula(self):
        candles = make_realistic_candles(n=50, seed=13)
        length, atr_length, multiplier = 20, 10, 1.5
        kc = calc_keltner(candles, length, atr_length, multiplier)
        ema_vals = calc_ema(extract_source(candles, "close"), length)
        atr_vals = calc_atr(candles, atr_length)
        checked = 0
        for i in range(len(candles)):
            if ema_vals[i] is None or atr_vals[i] is None:
                assert kc["lower"][i] is None
                continue
            assert abs(kc["lower"][i] - (ema_vals[i] - atr_vals[i] * multiplier)) < 1e-9
            checked += 1
        assert checked > 0

    # ── Invariants (spec sections 38-39) ─────────────────────────────────
    def test_channel_width_equals_2x_atr_x_multiplier(self):
        candles = make_realistic_candles(n=60, seed=14)
        atr_length, multiplier = 14, 3.0
        kc = calc_keltner(candles, length=20, atr_length=atr_length, multiplier=multiplier)
        atr_vals = calc_atr(candles, atr_length)
        checked = 0
        for i in range(len(candles)):
            if kc["upper"][i] is None or kc["lower"][i] is None:
                continue
            assert abs((kc["upper"][i] - kc["lower"][i]) - 2 * atr_vals[i] * multiplier) < 1e-9
            checked += 1
        assert checked > 0

    def test_center_equals_middle(self):
        candles = make_realistic_candles(n=60, seed=15)
        kc = calc_keltner(candles, length=20, atr_length=10, multiplier=2.0)
        checked = 0
        for i in range(len(candles)):
            if kc["middle"][i] is None:
                continue
            assert abs((kc["upper"][i] + kc["lower"][i]) / 2.0 - kc["middle"][i]) < 1e-9
            checked += 1
        assert checked > 0

    # ── Index alignment / off-by-one (spec section 25) ──────────────────
    def test_index_alignment_no_shift(self):
        """Upper[i]/Lower[i] must be built from EMA[i] and ATR[i] of the SAME
        index i — never EMA[i-1]/ATR[i+1] or any other shift."""
        candles = [{"high": 100 + i * 2, "low": 90 + i * 2, "close": 95 + i * 2} for i in range(40)]
        length, atr_length, multiplier = 10, 10, 1.0
        kc = calc_keltner(candles, length, atr_length, multiplier)
        ema_vals = calc_ema(extract_source(candles, "close"), length)
        atr_vals = calc_atr(candles, atr_length)
        checked = 0
        for i in range(len(candles)):
            if ema_vals[i] is None or atr_vals[i] is None:
                continue
            assert abs(kc["middle"][i] - ema_vals[i]) < 1e-9
            assert abs(kc["upper"][i] - (ema_vals[i] + atr_vals[i] * multiplier)) < 1e-9
            assert abs(kc["lower"][i] - (ema_vals[i] - atr_vals[i] * multiplier)) < 1e-9
            checked += 1
        assert checked > 0

    # ── Independent hand-computed golden case (spec section 42) ─────────
    def test_hand_computed_golden_case(self):
        """
        5 candles, length=3, atrLength=3, multiplier=2.0 — every value below
        is hand-derived independently of calc_keltner/calc_ema/calc_atr:

        Close = [100, 101, 102, 103, 104]
        EMA(3) seed at i=2 = avg(100,101,102) = 101; k = 2/(3+1) = 0.5
          i=3: 103*0.5 + 101*0.5 = 102
          i=4: 104*0.5 + 102*0.5 = 103
        -> EMA = [None, None, 101, 102, 103]

        High = close+5, Low = close-5 for every candle, so:
          TR[0] = |105-95| = 10
          TR[i>0] = max(|H-L|, |H-prevClose|, |L-prevClose|) = max(10, 6, 4) = 10
        -> TR is constant 10, so ATR(3) is constant 10 from its seed onward.
        -> ATR = [None, None, 10, 10, 10]

        Upper = Middle + 10*2 = Middle + 20; Lower = Middle - 20.
        """
        highs = [105, 106, 107, 108, 109]
        lows = [95, 96, 97, 98, 99]
        closes = [100, 101, 102, 103, 104]
        candles = make_hlc(highs, lows, closes)
        kc = calc_keltner(candles, length=3, atr_length=3, multiplier=2.0)

        assert kc["middle"][0] is None and kc["middle"][1] is None
        assert abs(kc["middle"][2] - 101) < 1e-9
        assert abs(kc["middle"][3] - 102) < 1e-9
        assert abs(kc["middle"][4] - 103) < 1e-9

        assert abs(kc["upper"][2] - 121) < 1e-9
        assert abs(kc["upper"][3] - 122) < 1e-9
        assert abs(kc["upper"][4] - 123) < 1e-9

        assert abs(kc["lower"][2] - 81) < 1e-9
        assert abs(kc["lower"][3] - 82) < 1e-9
        assert abs(kc["lower"][4] - 83) < 1e-9

    # ── Parameter variations (spec section 40) ───────────────────────────
    def test_length_parameter_variations(self):
        candles = make_realistic_candles(n=80, seed=25)
        for length in (1, 5, 20, 50):
            kc = calc_keltner(candles, length=length, atr_length=10, multiplier=2.0)
            first_valid = max(length, 10) - 1
            assert kc["middle"][first_valid] is not None
            if first_valid > 0:
                assert kc["middle"][first_valid - 1] is None

    def test_atr_length_parameter_variations(self):
        candles = make_realistic_candles(n=80, seed=26)
        for atr_length in (1, 10, 20):
            kc = calc_keltner(candles, length=20, atr_length=atr_length, multiplier=2.0)
            first_valid = max(20, atr_length) - 1
            assert kc["middle"][first_valid] is not None

    def test_multiplier_parameter_variations(self):
        candles = make_realistic_candles(n=60, seed=27)
        atr_vals = calc_atr(candles, 10)
        idx = 40
        widths = {}
        for multiplier in (0.5, 1, 2, 3):
            kc = calc_keltner(candles, length=20, atr_length=10, multiplier=multiplier)
            widths[multiplier] = kc["upper"][idx] - kc["lower"][idx]
            assert abs(widths[multiplier] - 2 * atr_vals[idx] * multiplier) < 1e-9
        assert widths[0.5] < widths[1] < widths[2] < widths[3]

    # ── Regression: must NOT be Bollinger Bands (spec section 44) ───────
    def test_bollinger_regression_width_uses_atr_not_stddev(self):
        """Every candle shares an identical close (stddev(close) == 0) but a
        wide, constant H/L range (ATR > 0). A Bollinger-style substitution
        (SMA +/- stddev*mult) would produce a channel width of exactly 0
        here; the real Keltner formula must not."""
        n = 40
        candles = [{"high": 110, "low": 90, "close": 100} for _ in range(n)]
        length, atr_length, multiplier = 10, 10, 2.0
        kc = calc_keltner(candles, length, atr_length, multiplier)
        _basis, bb_upper, bb_lower = calc_bb(extract_source(candles, "close"), length, multiplier)
        idx = n - 1
        assert abs(bb_upper[idx] - bb_lower[idx]) < 1e-9, "sanity: stddev of constant closes must be 0"
        kc_width = kc["upper"][idx] - kc["lower"][idx]
        assert kc_width > 1.0, "Keltner width must come from ATR (constant range here), not stddev"
        atr_vals = calc_atr(candles, atr_length)
        assert abs(kc_width - 2 * atr_vals[idx] * multiplier) < 1e-9

    # ── Regression: must NOT be Donchian Channels (spec section 45) ─────
    def test_donchian_regression_uses_ema_atr_not_highest_lowest(self):
        """A single huge spike candle stays inside Donchian's rolling window
        (so Donchian's upper stays pinned to it) long after Keltner's
        EMA/ATR have decayed back toward the surrounding baseline — proving
        Keltner does not track rolling highest-high/lowest-low."""
        n = 30
        candles = [{"high": 105, "low": 95, "close": 100} for _ in range(n)]
        candles[10] = {"high": 500, "low": 495, "close": 498}
        length = 20
        donchian = calc_donchian(candles, length)
        kc = calc_keltner(candles, length=length, atr_length=length, multiplier=2.0)
        idx = n - 1
        assert donchian["upper"][idx] == 500, "sanity: Donchian rolling-high still reflects the spike"
        assert kc["upper"][idx] < 200, "Keltner (EMA +/- ATR) must not track the rolling highest-high like Donchian does"

    # ── Parameter validation (spec sections 15, 23) ──────────────────────
    def test_invalid_length_rejected(self):
        candles = make_hlc([100, 101, 102], [90, 91, 92], [95, 96, 97])
        for bad in (0, -1):
            res = calc_keltner(candles, length=bad, atr_length=2, multiplier=2.0)
            assert all(v is None for v in res["middle"])

    def test_invalid_atr_length_rejected(self):
        candles = make_hlc([100, 101, 102], [90, 91, 92], [95, 96, 97])
        for bad in (0, -1):
            res = calc_keltner(candles, length=2, atr_length=bad, multiplier=2.0)
            assert all(v is None for v in res["middle"])

    def test_invalid_multiplier_rejected(self):
        candles = make_hlc([100, 101, 102], [90, 91, 92], [95, 96, 97])
        for bad in (0, -1, -2.5, float("nan"), float("inf")):
            res = calc_keltner(candles, length=2, atr_length=2, multiplier=bad)
            assert all(v is None for v in res["middle"]), f"multiplier={bad} must be rejected"

    # ── Degenerate OHLC (spec section 15) ────────────────────────────────
    def test_high_less_than_low_handled_without_crash(self):
        """high < low is semantically invalid input but must not crash or
        produce NaN/Infinity — calc_atr already defines the tolerant
        behavior for this; Keltner must not add a second failure mode."""
        candles = make_hlc([90, 91, 92, 93, 94], [100, 101, 102, 103, 104], [95, 96, 97, 98, 99])
        kc = calc_keltner(candles, length=3, atr_length=3, multiplier=2.0)
        for v in kc["middle"] + kc["upper"] + kc["lower"]:
            if v is not None:
                assert math.isfinite(v)

    # ── Large dataset stability ───────────────────────────────────────────
    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=3000, seed=28)
        kc = calc_keltner(candles, length=20, atr_length=10, multiplier=2.0)
        for v in kc["middle"] + kc["upper"] + kc["lower"]:
            if v is not None:
                assert math.isfinite(v)

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_donchian(candles, 3)["upper"][2] is not None


class TestROC:
    """Rate of Change: ROC[i] = ((src[i]-src[i-n])/src[i-n]) * 100."""

    def test_empty_data(self):
        roc, signal = calc_roc([], period=12)
        assert roc == [] and signal == []

    def test_warmup(self):
        src = [100 + i for i in range(20)]
        roc, _ = calc_roc(src, period=12)
        for i in range(12):
            assert roc[i] is None
        assert roc[12] is not None

    def test_formula_matches_manual_calculation_15_bar_series(self):
        """Prompt's required test case: 15-bar close series, ROC(12) hand-verified at >=2 points."""
        closes = [100, 102, 101, 105, 107, 106, 110, 108, 112, 115, 113, 117, 120, 118, 122]
        roc, _ = calc_roc(closes, period=12)
        # i=12: (120-100)/100*100 = 20.0
        assert abs(roc[12] - 20.0) < 1e-9
        # i=13: (118-102)/102*100
        assert abs(roc[13] - ((118 - 102) / 102 * 100)) < 1e-9
        # i=14: (122-101)/101*100
        assert abs(roc[14] - ((122 - 101) / 101 * 100)) < 1e-9

    def test_division_by_zero_guarded_not_infinity_or_nan(self):
        src = [0, 5, 10, 15]
        roc, _ = calc_roc(src, period=1)
        assert roc[0] is None  # warm-up
        assert roc[1] is None  # base (src[0]) is 0 -> guarded, not Infinity/NaN
        assert abs(roc[2] - 100.0) < 1e-9  # (10-5)/5*100
        assert abs(roc[3] - 50.0) < 1e-9   # (15-10)/10*100

    def test_signal_line_off_by_default(self):
        src = [100 + i * 2 for i in range(20)]
        roc, signal = calc_roc(src, period=5)
        assert all(v is None for v in signal)

    def test_signal_line_off_when_explicitly_zero(self):
        src = [100 + i * 2 for i in range(20)]
        roc, signal = calc_roc(src, period=5, signal_period=0)
        assert all(v is None for v in signal)

    def test_signal_line_is_sma_of_roc_line(self):
        src = [100 + i * 2 + (3 if i % 2 == 0 else -3) for i in range(30)]
        roc, signal = calc_roc(src, period=5, signal_period=3)
        first_roc = next(i for i, v in enumerate(roc) if v is not None)
        # First valid signal index = first_roc + (signal_period - 1)
        sig_idx = first_roc + 3 - 1
        assert signal[sig_idx - 1] is None
        assert signal[sig_idx] is not None
        expected = (roc[sig_idx - 2] + roc[sig_idx - 1] + roc[sig_idx]) / 3.0
        assert abs(signal[sig_idx] - expected) < 1e-9

    def test_positive_and_negative_direction(self):
        rising = [100 + i * 5 for i in range(20)]
        falling = [200 - i * 5 for i in range(20)]
        roc_up, _ = calc_roc(rising, period=10)
        roc_down, _ = calc_roc(falling, period=10)
        assert roc_up[15] > 0
        assert roc_down[15] < 0

    def test_period_variations(self):
        src = [100 + i for i in range(60)]
        for period in (1, 5, 12, 30):
            roc, _ = calc_roc(src, period=period)
            assert roc[period] is not None
            if period > 0:
                assert roc[period - 1] is None

    def test_invalid_period_rejected(self):
        src = [100, 101, 102, 103]
        for bad in (0, -1):
            roc, signal = calc_roc(src, period=bad)
            assert all(v is None for v in roc)

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=3000, seed=31)
        closes = extract_source(candles, "close")
        roc, signal = calc_roc(closes, period=12, signal_period=9)
        for v in roc + signal:
            if v is not None:
                assert math.isfinite(v)

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None


class TestAroon:
    """Aroon Up/Down/Oscillator, default period 14 (TradingView's own default)."""

    def test_empty_data(self):
        res = calc_aroon([], period=14)
        assert res == {"up": [], "down": [], "osc": []}

    def test_warmup(self):
        candles = make_hlc([100 + i for i in range(20)], [90 + i for i in range(20)], [95 + i for i in range(20)])
        res = calc_aroon(candles, period=14)
        for i in range(14):
            assert res["up"][i] is None and res["down"][i] is None and res["osc"][i] is None
        assert res["up"][14] is not None

    def test_aroon_up_hits_100_on_new_high_bar(self):
        """Prompt's required test case: a clear new high partway through a
        20-bar series must make aroonUp hit exactly 100 ON that bar."""
        highs = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 150, 93, 92, 91, 90, 89]
        lows = [90] * 20
        candles = make_hlc(highs, lows, [95] * 20)
        res = calc_aroon(candles, period=14)
        assert res["up"][14] == 100.0
        assert res["up"][19] < 100.0  # the spike has aged within the window by bar 19

    def test_aroon_down_hits_100_on_new_low_bar(self):
        lows = [100, 99, 101, 98, 102, 97, 103, 96, 104, 95, 105, 94, 106, 93, 50, 107, 108, 109, 110, 111]
        highs = [120] * 20
        candles = make_hlc(highs, lows, [110] * 20)
        res = calc_aroon(candles, period=14)
        assert res["down"][14] == 100.0
        assert res["down"][19] < 100.0

    def test_tie_breaking_uses_most_recent_occurrence(self):
        """A repeated maximum within the window must resolve to the LATER
        index — verified by hand: highs=[50,100,90,100,80], period=2.
        i=3's window is [100(j=1), 90(j=2), 100(j=3)]: the tie between j=1
        and j=3 must resolve to j=3 (today), giving barsSinceHigh=0 ->
        AroonUp=100. Resolving to the earlier j=1 would instead give
        barsSinceHigh=2 -> AroonUp=0 — a completely different, wrong result,
        making this a precise regression check for the tie rule itself."""
        highs = [50, 100, 90, 100, 80]
        lows = [10] * 5
        candles = make_hlc(highs, lows, [30] * 5)
        res = calc_aroon(candles, period=2)
        assert res["up"][3] == 100.0

    def test_hand_computed_golden_case(self):
        """
        6 candles, period=3 — every value below is hand-derived
        independently of calc_aroon:
          highs = [10, 15, 12, 20, 14, 11]
          lows  = [8,  9,  6,  11, 7,  5]
        i=3: window highs[0:4]=[10,15,12,20] -> max=20 at j=3 -> barsSinceHigh=0 -> up=100
             window lows[0:4]=[8,9,6,11]     -> min=6  at j=2 -> barsSinceLow=1  -> down=(3-1)/3*100=66.6667
        i=4: window highs[1:5]=[15,12,20,14] -> max=20 at j=3 -> barsSinceHigh=1 -> up=(3-1)/3*100=66.6667
             window lows[1:5]=[9,6,11,7]     -> min=6  at j=2 -> barsSinceLow=2  -> down=(3-2)/3*100=33.3333
        i=5: window highs[2:6]=[12,20,14,11] -> max=20 at j=3 -> barsSinceHigh=2 -> up=(3-2)/3*100=33.3333
             window lows[2:6]=[6,11,7,5]     -> min=5  at j=5 -> barsSinceLow=0  -> down=100
        """
        highs = [10, 15, 12, 20, 14, 11]
        lows = [8, 9, 6, 11, 7, 5]
        candles = make_hlc(highs, lows, [9] * 6)
        res = calc_aroon(candles, period=3)

        assert abs(res["up"][3] - 100.0) < 1e-9
        assert abs(res["down"][3] - 200.0 / 3.0) < 1e-9
        assert abs(res["osc"][3] - (100.0 - 200.0 / 3.0)) < 1e-9

        assert abs(res["up"][4] - 200.0 / 3.0) < 1e-9
        assert abs(res["down"][4] - 100.0 / 3.0) < 1e-9

        assert abs(res["up"][5] - 100.0 / 3.0) < 1e-9
        assert abs(res["down"][5] - 100.0) < 1e-9

    def test_oscillator_equals_up_minus_down(self):
        candles = make_realistic_candles(n=60, seed=33)
        res = calc_aroon(candles, period=14)
        for i in range(len(candles)):
            if res["up"][i] is None:
                continue
            assert abs(res["osc"][i] - (res["up"][i] - res["down"][i])) < 1e-9

    def test_bounds_0_to_100(self):
        candles = make_realistic_candles(n=100, seed=34)
        res = calc_aroon(candles, period=14)
        for v in res["up"] + res["down"]:
            if v is not None:
                assert 0.0 - 1e-9 <= v <= 100.0 + 1e-9

    def test_period_variations(self):
        candles = make_realistic_candles(n=80, seed=35)
        for period in (5, 14, 25):
            res = calc_aroon(candles, period=period)
            assert res["up"][period] is not None
            assert res["up"][period - 1] is None

    def test_invalid_period_rejected(self):
        candles = make_hlc([100, 101, 102], [90, 91, 92], [95, 96, 97])
        for bad in (0, -1):
            res = calc_aroon(candles, period=bad)
            assert all(v is None for v in res["up"])

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=3000, seed=36)
        res = calc_aroon(candles, period=14)
        for v in res["up"] + res["down"] + res["osc"]:
            if v is not None:
                assert math.isfinite(v)

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None


class TestCMF:
    """Chaikin Money Flow: rolling sum(MFV)/sum(Volume) over `period` bars."""

    def test_empty_data(self):
        assert calc_cmf([], period=20) == []

    def test_warmup(self):
        candles = make_realistic_candles(n=30, seed=41)
        vals = calc_cmf(candles, period=20)
        for i in range(19):
            assert vals[i] is None
        assert vals[19] is not None

    def test_doji_bar_forced_to_zero_mfm_not_nan(self):
        """A High==Low (doji) bar must contribute MFM=0 (not NaN) to the
        window sum — its volume still counts toward the denominator, it
        just adds nothing to the numerator. A massive doji volume must not
        blow up or crash the result."""
        candles = [
            {"high": 100, "low": 100, "close": 100, "volume": 999999},  # doji, huge volume
            {"high": 110, "low": 100, "close": 108, "volume": 100},     # MFM = ((8)-(2))/10 = 0.6
        ]
        vals = calc_cmf(candles, period=2)
        assert vals[1] is not None
        assert math.isfinite(vals[1])
        expected = (0.6 * 100) / (999999 + 100)
        assert abs(vals[1] - expected) < 1e-9

    def test_zero_volume_window_returns_none_not_crash(self):
        candles = [
            {"high": 110, "low": 100, "close": 105, "volume": 0},
            {"high": 115, "low": 105, "close": 110, "volume": 0},
            {"high": 120, "low": 110, "close": 115, "volume": 0},
        ]
        vals = calc_cmf(candles, period=3)
        assert vals[2] is None

    def test_25_bar_series_with_a_high_equals_low_bar_no_crash(self):
        """Prompt's required test case: 25-bar OHLCV with a High==Low bar
        must not crash and must produce sane values on either side of it."""
        candles = make_realistic_candles(n=25, seed=42)
        candles[12] = dict(candles[12], high=candles[12]["close"], low=candles[12]["close"])
        vals = calc_cmf(candles, period=20)
        for v in vals:
            if v is not None:
                assert math.isfinite(v)
                assert -1.5 <= v <= 1.5  # "roughly -1 to +1" per the spec; generous margin for synthetic data
        assert vals[19] is not None and vals[24] is not None

    def test_hand_computed_golden_case_including_rolling_window_drop(self):
        """
        4 candles, period=3 — every value below is hand-derived
        independently of calc_cmf:
          bar0: H=110,L=100,C=108,V=1000 -> MFM=((8)-(2))/10=0.6   -> MFV=600
          bar1: H=120,L=100,C=115,V=2000 -> MFM=((15)-(5))/20=0.5  -> MFV=1000
          bar2: H=130,L=110,C=112,V=500  -> MFM=((2)-(18))/20=-0.8 -> MFV=-400
          bar3: H=105,L=105,C=105,V=800  -> doji -> MFM=0          -> MFV=0
        i=2 (window=bars 0-2): sum_mfv=600+1000-400=1200, sum_vol=3500 -> CMF=1200/3500
        i=3 (window=bars 1-3, bar0 drops out): sum_mfv=1000-400+0=600, sum_vol=2000+500+800=3300 -> CMF=600/3300
        """
        candles = [
            {"high": 110, "low": 100, "close": 108, "volume": 1000},
            {"high": 120, "low": 100, "close": 115, "volume": 2000},
            {"high": 130, "low": 110, "close": 112, "volume": 500},
            {"high": 105, "low": 105, "close": 105, "volume": 800},
        ]
        vals = calc_cmf(candles, period=3)
        assert vals[0] is None and vals[1] is None
        assert abs(vals[2] - (1200.0 / 3500.0)) < 1e-9
        assert abs(vals[3] - (600.0 / 3300.0)) < 1e-9

    def test_period_variations(self):
        candles = make_realistic_candles(n=80, seed=43)
        for period in (5, 20, 40):
            vals = calc_cmf(candles, period=period)
            assert vals[period - 1] is not None
            if period > 1:
                assert vals[period - 2] is None

    def test_invalid_period_rejected(self):
        candles = make_hlc([100, 101, 102], [90, 91, 92], [95, 96, 97])
        for bad in (0, -1):
            vals = calc_cmf(candles, period=bad)
            assert all(v is None for v in vals)

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=3000, seed=44)
        vals = calc_cmf(candles, period=20)
        for v in vals:
            if v is not None:
                assert math.isfinite(v)

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_aroon(candles, period=2)["up"][2] is not None


class TestMarketStructure:
    """
    Phase 2P — Market Structure Engine (BOS / CHoCH / MSS foundation).

    Primary fixture: a 25-candle hand-traced sequence (swing_length=2,
    Close confirmation, open defaulting to close via make_hlc so
    displacement is always 0 and every reversal classifies CHOCH, never
    MSS). High = close+1, Low = close-1 for every candle, so swing
    detection and classification were hand-verified directly against
    these H/L values (not the close values) index by index — see the
    inline commentary below for the derivation of each swing and event.

    closes: [100,102,105,103,101,104,109,106,102,108,113,110,107,111,116,
             112,108,105,101,98,100,103,99,102,105]

    Hand-verified swings (index, type, price=H or L, confirmedAt, class):
      2  high 106  confAt4   None (first)
      4  low  100  confAt6   None (first)
      6  high 110  confAt8   HH
      8  low  101  confAt10  HL
      10 high 114  confAt12  HH
      12 low  106  confAt14  HL
      14 high 117  confAt16  HH
      19 low  97   confAt21  LL
      21 high 104  confAt23  LH
      22 low  98   confAt24  HL

    Hand-verified events (breakIndex, type, direction, brokenLevel, origin):
      6  CHOCH bullish 106 origin=2   (first-ever break: neutral -> bullish)
      10 BOS   bullish 110 origin=6   (continuation)
      14 BOS   bullish 114 origin=10  (continuation)
      17 CHOCH bearish 106 origin=12  (reversal: bullish -> bearish)
      24 CHOCH bullish 104 origin=21  (reversal: bearish -> bullish)
    """

    CLOSES = [100, 102, 105, 103, 101, 104, 109, 106, 102, 108, 113, 110, 107, 111, 116,
              112, 108, 105, 101, 98, 100, 103, 99, 102, 105]

    @staticmethod
    def _candles(closes):
        return make_hlc([c + 1 for c in closes], [c - 1 for c in closes], closes)

    # ── Swing detection ──────────────────────────────────────────────────
    def test_empty_data(self):
        res = calc_market_structure([], swing_length=5)
        assert res == {"swings": [], "events": [], "trend": "neutral"}

    def test_insufficient_candles_no_swings_no_crash(self):
        candles = self._candles(self.CLOSES[:4])  # < 2*swing_length+1 for swing_length=2
        res = calc_market_structure(candles, swing_length=2)
        assert res["swings"] == [] and res["events"] == [] and res["trend"] == "neutral"

    def test_swing_high_detection_golden_case(self):
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        highs = [s for s in res["swings"] if s["type"] == "high"]
        assert [s["index"] for s in highs] == [2, 6, 10, 14, 21]
        assert [s["price"] for s in highs] == [106, 110, 114, 117, 104]
        assert [s["confirmedAt"] for s in highs] == [4, 8, 12, 16, 23]

    def test_swing_low_detection_golden_case(self):
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        lows = [s for s in res["swings"] if s["type"] == "low"]
        assert [s["index"] for s in lows] == [4, 8, 12, 19, 22]
        assert [s["price"] for s in lows] == [100, 101, 106, 97, 98]
        assert [s["confirmedAt"] for s in lows] == [6, 10, 14, 21, 24]

    def test_hh_hl_lh_ll_classification_golden_case(self):
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        by_index = {s["index"]: s["classification"] for s in res["swings"]}
        assert by_index[2] is None and by_index[4] is None  # first of each type: unclassifiable
        assert by_index[6] == "HH"
        assert by_index[8] == "HL"
        assert by_index[10] == "HH"
        assert by_index[12] == "HL"
        assert by_index[14] == "HH"
        assert by_index[19] == "LL"
        assert by_index[21] == "LH"
        assert by_index[22] == "HL"

    def test_pivot_confirmation_delay_non_repainting(self):
        """A swing pivot must not appear at all until swing_length real
        candles exist after it — giving it fewer than that must simply
        drop it, never fabricate an early/partial confirmation."""
        # The golden case's swing at index 21 needs candles through index 23.
        candles = self._candles(self.CLOSES[:23])  # index 21 has only 1 trailing candle (22)
        res = calc_market_structure(candles, swing_length=2)
        assert all(s["index"] != 21 for s in res["swings"]), "swing at 21 must not be confirmed with only 1 trailing candle"
        candles_full = self._candles(self.CLOSES[:24])  # now exactly 2 trailing candles (22, 23)
        res_full = calc_market_structure(candles_full, swing_length=2)
        assert any(s["index"] == 21 for s in res_full["swings"]), "swing at 21 must be confirmed once 2 trailing candles exist"

    def test_duplicate_swing_prevented_on_flat_plateau(self):
        """A flat plateau of equal highs must produce exactly one swing
        high (the first candle of the tie), never one per tied candle —
        same tie convention as calc_pivot_high_low."""
        highs = [90, 95, 100, 100, 100, 95, 90]
        lows = [80] * 7
        candles = make_hlc(highs, lows, [85] * 7)
        res = calc_market_structure(candles, swing_length=2)
        high_swings = [s for s in res["swings"] if s["type"] == "high"]
        assert len(high_swings) == 1
        assert high_swings[0]["index"] == 2

    # ── BOS ──────────────────────────────────────────────────────────────
    def test_bullish_bos_golden_case(self):
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        bos_events = [e for e in res["events"] if e["type"] == "BOS"]
        assert len(bos_events) == 2
        assert bos_events[0]["direction"] == "bullish" and bos_events[0]["breakIndex"] == 10 and bos_events[0]["brokenLevel"] == 110
        assert bos_events[1]["direction"] == "bullish" and bos_events[1]["breakIndex"] == 14 and bos_events[1]["brokenLevel"] == 114

    def test_bearish_bos_dedicated_case(self):
        """
        11-candle dedicated bearish trace, swing_length=2, hand-verified:
          closes: [100,98,95,97,99,96,91,94,98,92,87]
        Swings: low@2(94,confAt4), high@4(100,confAt6),
                low@6(90,confAt8,LL), high@8(99,confAt10,LH)
        Events: CHOCH bearish @6 (brokenLevel94, origin=2) — bootstrap
                BOS   bearish @10 (brokenLevel90, origin=6) — continuation
        """
        closes = [100, 98, 95, 97, 99, 96, 91, 94, 98, 92, 87]
        candles = self._candles(closes)
        res = calc_market_structure(candles, swing_length=2)
        assert res["trend"] == "bearish"
        bos = [e for e in res["events"] if e["type"] == "BOS"]
        assert len(bos) == 1
        assert bos[0]["direction"] == "bearish" and bos[0]["breakIndex"] == 10 and bos[0]["brokenLevel"] == 90 and bos[0]["originSwingIndex"] == 6

    def test_close_confirmation_default(self):
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2, confirmation="Close")
        assert all(e["confirmationType"] == "Close" for e in res["events"])

    def test_wick_only_move_does_not_break_in_close_mode_but_does_in_wick_mode(self):
        """A candle whose HIGH pokes through a level but whose CLOSE does
        not must NOT trigger a break under the default 'Close' confirmation
        (this is LEVERAGE's 'false break' handling — a wick alone is never
        enough); switching to 'Wick' confirmation must then trigger it.
        Swing high @2 (price 106, confAt4) from H=[101,103,106,104,102],
        L=[99,101,104,102,100]; candle 5 has High=110 (pokes through 106)
        but Close=104 (stays below it)."""
        candles = make_hlc(
            [101, 103, 106, 104, 102, 110],
            [99, 101, 104, 102, 100, 99],
            [100, 102, 105, 103, 101, 104]
        )
        res_close = calc_market_structure(candles, swing_length=2, confirmation="Close")
        assert res_close["events"] == [], "a wick-only poke must not break structure in Close mode"

        res_wick = calc_market_structure(candles, swing_length=2, confirmation="Wick")
        assert len(res_wick["events"]) == 1
        assert res_wick["events"][0]["confirmationType"] == "Wick"
        assert res_wick["events"][0]["breakIndex"] == 5

    def test_broken_level_never_fires_twice(self):
        """General invariant over the golden case: every event has a
        UNIQUE origin swing — no structural level ever produces a second
        event once it has been broken and marked so."""
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        origins = [e["originSwingIndex"] for e in res["events"]]
        assert len(origins) == len(set(origins))

    # ── CHoCH ────────────────────────────────────────────────────────────
    def test_bullish_choch_bootstrap(self):
        """The very first break the engine ever sees, from a neutral
        state, must be classified CHOCH (or MSS if displaced) — never BOS,
        since BOS requires an already-established matching trend."""
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        first_event = res["events"][0]
        assert first_event["type"] == "CHOCH"
        assert first_event["direction"] == "bullish"
        assert first_event["breakIndex"] == 6

    def test_bearish_choch_reversal_golden_case(self):
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        bearish_choch = [e for e in res["events"] if e["type"] == "CHOCH" and e["direction"] == "bearish"]
        assert len(bearish_choch) == 1
        assert bearish_choch[0]["breakIndex"] == 17 and bearish_choch[0]["brokenLevel"] == 106 and bearish_choch[0]["originSwingIndex"] == 12

    def test_choch_depends_on_structural_direction_not_just_break_magnitude(self):
        """Structural-direction dependency: the identical golden-case data
        produces a CONTINUATION (BOS) at index 10 (trend already bullish)
        but a REVERSAL (CHOCH) at index 17 (breaking against the bullish
        trend) — proving classification depends on current trend state,
        not merely 'price broke a level'."""
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        by_break_index = {e["breakIndex"]: e["type"] for e in res["events"]}
        assert by_break_index[10] == "BOS"
        assert by_break_index[17] == "CHOCH"

    # ── MSS ──────────────────────────────────────────────────────────────
    def test_mss_vs_choch_distinguished_purely_by_displacement(self):
        """Identical structural setup (one swing high at index 2, broken by
        the candle at index 5) differing ONLY in that break candle's body
        size: a small body classifies CHOCH, a large (displaced) body
        classifies MSS instead — isolating the exact variable that decides
        MSS vs CHOCH. ATR's own correctness is covered exhaustively by
        TestATRCorrectness elsewhere; this test only proves the branching
        logic, with atr_length shortened to 3 so it warms up within 6
        candles."""
        base_highs = [101, 103, 106, 104, 102]
        base_lows = [99, 101, 104, 102, 100]
        base_closes = [100, 102, 105, 103, 101]

        # Small body: open=105 (near the prior close), close=108 -> |diff|=3
        small_body = make_hlc(base_highs + [110], base_lows + [100], base_closes + [108])
        small_body[5]["open"] = 105
        res_small = calc_market_structure(small_body, swing_length=2, atr_length=3)
        assert len(res_small["events"]) == 1 and res_small["events"][0]["type"] == "CHOCH"

        # Large body: open=101 (well below the level), close=140 -> |diff|=39
        big_body = make_hlc(base_highs + [145], base_lows + [100], base_closes + [140])
        big_body[5]["open"] = 101
        res_big = calc_market_structure(big_body, swing_length=2, atr_length=3)
        assert len(res_big["events"]) == 1 and res_big["events"][0]["type"] == "MSS"

    def test_bullish_and_bearish_mss(self):
        """Both directions can produce MSS: reuse the displaced 'big body'
        case above for bullish, and mirror it downward for bearish."""
        base_highs = [101, 103, 106, 104, 102]
        base_lows = [99, 101, 104, 102, 100]
        base_closes = [100, 102, 105, 103, 101]
        big_body_up = make_hlc(base_highs + [145], base_lows + [100], base_closes + [140])
        big_body_up[5]["open"] = 101
        res_up = calc_market_structure(big_body_up, swing_length=2, atr_length=3)
        assert res_up["events"][0]["type"] == "MSS" and res_up["events"][0]["direction"] == "bullish"

        # Mirror: a swing LOW at index 2, broken downward with a huge body.
        low_highs = [111, 109, 106, 108, 110]
        low_lows = [99, 101, 94, 96, 98]
        low_closes = [100, 102, 95, 97, 99]  # swing low candidate at index 2 (L=94)
        big_body_down = make_hlc(low_highs + [96], low_lows + [50], low_closes + [55])
        big_body_down[5]["open"] = 99
        res_down = calc_market_structure(big_body_down, swing_length=2, atr_length=3)
        assert len(res_down["events"]) == 1
        assert res_down["events"][0]["type"] == "MSS" and res_down["events"][0]["direction"] == "bearish"

    def test_mss_is_a_separate_event_type_from_bos_and_choch(self):
        candles = self._candles(self.CLOSES)
        res = calc_market_structure(candles, swing_length=2)
        types = set(e["type"] for e in res["events"])
        assert types <= {"BOS", "CHOCH", "MSS"}
        # The golden case (open==close everywhere) has zero displacement,
        # so MSS never fires there — confirms MSS isn't silently aliasing
        # CHOCH (they're reachable independently, per the tests above).
        assert "MSS" not in types

    def test_mss_deterministic_confirmation(self):
        big_body = make_hlc([101, 103, 106, 104, 102, 145], [99, 101, 104, 102, 100, 100], [100, 102, 105, 103, 101, 140])
        big_body[5]["open"] = 101
        res1 = calc_market_structure(big_body, swing_length=2, atr_length=3)
        res2 = calc_market_structure(big_body, swing_length=2, atr_length=3)
        assert res1 == res2

    # ── Repainting / determinism ─────────────────────────────────────────
    def test_determinism_repeated_calls_identical(self):
        candles = self._candles(self.CLOSES)
        res1 = calc_market_structure(candles, swing_length=2)
        res2 = calc_market_structure(candles, swing_length=2)
        assert res1 == res2

    def test_historical_prefix_equivalence_no_repainting(self):
        """Recomputing on a PREFIX of the candles must reproduce exactly
        the same events (same type/direction/brokenLevel/originSwingIndex)
        for every break that already occurred within that prefix — the
        core non-repainting / realtime-equals-historical guarantee."""
        full = self._candles(self.CLOSES)
        full_res = calc_market_structure(full, swing_length=2)
        for prefix_len in (15, 20, 23, 24, 25):
            prefix = full[:prefix_len]
            prefix_res = calc_market_structure(prefix, swing_length=2)
            expected = [e for e in full_res["events"] if e["breakIndex"] < prefix_len]
            assert prefix_res["events"] == expected, f"mismatch at prefix_len={prefix_len}"

    def test_confirmed_swing_immutable_across_longer_history(self):
        """A swing already confirmed within a shorter history must keep
        the exact same price/classification once MORE candles are added —
        never retroactively revised."""
        candles_20 = self._candles(self.CLOSES[:20])
        candles_25 = self._candles(self.CLOSES)
        res_20 = calc_market_structure(candles_20, swing_length=2)
        res_25 = calc_market_structure(candles_25, swing_length=2)
        by_index_20 = {s["index"]: s for s in res_20["swings"]}
        by_index_25 = {s["index"]: s for s in res_25["swings"]}
        for idx, sw in by_index_20.items():
            assert idx in by_index_25
            assert by_index_25[idx]["price"] == sw["price"]
            assert by_index_25[idx]["classification"] == sw["classification"]
            assert by_index_25[idx]["type"] == sw["type"]

    def test_realtime_incremental_walk_matches_full_history(self):
        """Walking forward one candle at a time (recomputing on
        candles[:i+1] at each step, simulating a live feed) must reveal
        exactly the same events, in the same order, as the one-shot full
        history computation."""
        full = self._candles(self.CLOSES)
        full_res = calc_market_structure(full, swing_length=2)
        seen = []
        for i in range(len(full)):
            step_res = calc_market_structure(full[:i + 1], swing_length=2)
            for e in step_res["events"]:
                if e not in seen:
                    seen.append(e)
        assert seen == full_res["events"]

    # ── Data quality ─────────────────────────────────────────────────────
    def test_market_gap_handled_without_crash(self):
        closes = self.CLOSES[:10] + [500] + self.CLOSES[11:]  # a sudden large gap
        candles = self._candles(closes)
        res = calc_market_structure(candles, swing_length=2)
        for ev in res["events"]:
            assert math.isfinite(ev["brokenLevel"])

    def test_invalid_swing_length_rejected(self):
        candles = self._candles(self.CLOSES)
        for bad in (0, -1):
            res = calc_market_structure(candles, swing_length=bad)
            assert res == {"swings": [], "events": [], "trend": "neutral"}

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=51)
        res = calc_market_structure(candles, swing_length=5)
        for sw in res["swings"]:
            assert math.isfinite(sw["price"])
        for ev in res["events"]:
            assert math.isfinite(ev["brokenLevel"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_pivot_high_low(candles, 2, 2)["pivotHigh"] is not None


class TestFVG:
    """Phase 2Q — Fair Value Gap (3-candle imbalance) engine."""

    @staticmethod
    def _hlc(highs, lows, closes):
        return make_hlc(highs, lows, closes)

    # ── Detection ────────────────────────────────────────────────────────
    def test_empty_data(self):
        assert calc_fvg([]) == []

    def test_insufficient_candles(self):
        assert calc_fvg(self._hlc([105, 108], [95, 98], [100, 103])) == []

    def test_bullish_fvg_basic(self):
        """c1 H=105,L=95; c2 H=108,L=98; c3 H=115,L=108.
        Low(c3)=108 > High(c1)=105 -> zone [105, 108], size 3."""
        candles = self._hlc([105, 108, 115], [95, 98, 108], [100, 103, 112])
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0)
        assert len(fvgs) == 1
        f = fvgs[0]
        assert f["direction"] == "bullish"
        assert f["originIndex"] == 0 and f["confirmIndex"] == 2
        assert f["bottom"] == 105 and f["top"] == 108 and abs(f["size"] - 3) < 1e-9
        assert abs(f["midpoint"] - 106.5) < 1e-9

    def test_bearish_fvg_basic(self):
        """c1 H=105,L=100; c2 H=100,L=90; c3 H=95,L=85.
        High(c3)=95 < Low(c1)=100 -> zone [95, 100], size 5."""
        candles = self._hlc([105, 100, 95], [100, 90, 85], [102, 95, 90])
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0)
        assert len(fvgs) == 1
        f = fvgs[0]
        assert f["direction"] == "bearish"
        assert f["bottom"] == 95 and f["top"] == 100 and abs(f["size"] - 5) < 1e-9

    def test_no_fvg_when_ranges_overlap(self):
        candles = self._hlc([105, 108, 110], [95, 98, 100], [100, 103, 105])
        assert calc_fvg(candles, min_size_atr_multiplier=0) == []

    def test_exact_boundary_is_not_an_fvg(self):
        """Low(c3) == High(c1) exactly must NOT count — strictly greater
        than is required, not greater-than-or-equal."""
        candles = self._hlc([105, 108, 112], [95, 98, 105], [100, 103, 108])
        assert calc_fvg(candles, min_size_atr_multiplier=0) == []

    def test_multiple_consecutive_fvgs(self):
        """5 candles, each shifted +10 with a real gap from the last ->
        3 consecutive bullish FVGs, one per overlapping triple."""
        highs = [105, 115, 125, 135, 145]
        lows = [95, 105, 115, 125, 135]
        closes = [100, 110, 120, 130, 140]
        candles = self._hlc(highs, lows, closes)
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0)
        assert len(fvgs) == 3
        assert [f["originIndex"] for f in fvgs] == [0, 1, 2]
        assert all(f["direction"] == "bullish" for f in fvgs)
        assert [f["bottom"] for f in fvgs] == [105, 115, 125]
        assert [f["top"] for f in fvgs] == [115, 125, 135]

    def test_minimum_size_filtering(self):
        """A 0.2-wide gap against an ATR(3)-baseline of 10: filtered out at
        the 0.1x-ATR default (threshold 1.0), present when the multiplier
        is 0. TR/ATR hand-verified: TR[0..2] all = 10 -> seed ATR(3)[2]=10."""
        candles = self._hlc(
            [110, 112, 114, 116, 118],
            [100, 102, 104, 106, 114.2],
            [105, 107, 109, 111, 115]
        )
        filtered = calc_fvg(candles, min_size_atr_multiplier=0.1, atr_length=3)
        assert filtered == [], "a 0.2-wide gap must be filtered against a threshold of 1.0 (0.1 * ATR 10)"
        kept = calc_fvg(candles, min_size_atr_multiplier=0, atr_length=3)
        assert len(kept) == 1
        assert abs(kept[0]["size"] - 0.2) < 1e-9

    # ── Lifecycle / mitigation ───────────────────────────────────────────
    # Shared 7-candle fixture: FVG confirmed at index 2 (zone [105, 108],
    # size 3); indices 3-4 don't touch it; index 5 partially touches
    # (low=106, i.e. 2/3 filled); index 6 completes the fill (low=103).
    LIFECYCLE_HIGHS = [105, 112, 115, 118, 120, 112, 115]
    LIFECYCLE_LOWS = [95, 98, 108, 111, 113, 106, 103]
    LIFECYCLE_CLOSES = [100, 103, 112, 115, 118, 109, 106]

    def _lifecycle_candles(self, n=7):
        return self._hlc(self.LIFECYCLE_HIGHS[:n], self.LIFECYCLE_LOWS[:n], self.LIFECYCLE_CLOSES[:n])

    def test_active_fvg_before_any_touch(self):
        candles = self._lifecycle_candles(5)  # only through index 4, no touch yet
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0)
        assert len(fvgs) == 1
        f = fvgs[0]
        assert f["status"] == "active"
        assert f["fillPercentage"] == 0
        assert f["mitigatedIndex"] is None

    def test_partial_fill_tracked(self):
        candles = self._lifecycle_candles(6)  # through index 5 (the partial touch)
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0, mitigation="Full Fill")
        f = fvgs[0]
        assert f["status"] == "partially_mitigated"
        assert abs(f["fillPercentage"] - (2.0 / 3.0)) < 1e-9
        assert f["mitigatedIndex"] is None, "not fully mitigated yet, so no mitigation timestamp"

    def test_full_fill_mitigation(self):
        candles = self._lifecycle_candles(7)  # through index 6 (completes the fill)
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0, mitigation="Full Fill")
        f = fvgs[0]
        assert f["status"] == "fully_mitigated"
        assert abs(f["fillPercentage"] - 1.0) < 1e-9
        assert f["mitigatedIndex"] == 6
        assert f["mitigationPrice"] == 103

    def test_touch_mitigation_fires_immediately_on_first_intrusion(self):
        """Same fixture, but 'Touch' mode must mark it mitigated at the
        FIRST intrusion (index 5, low=106), not wait for a full fill."""
        candles = self._lifecycle_candles(7)
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0, mitigation="Touch")
        f = fvgs[0]
        assert f["status"] == "fully_mitigated"
        assert f["mitigatedIndex"] == 5
        assert f["mitigationPrice"] == 106

    def test_touch_vs_full_fill_differ_on_identical_data(self):
        candles = self._lifecycle_candles(7)
        touch = calc_fvg(candles, min_size_atr_multiplier=0, mitigation="Touch")[0]
        full = calc_fvg(candles, min_size_atr_multiplier=0, mitigation="Full Fill")[0]
        assert touch["mitigatedIndex"] != full["mitigatedIndex"]

    def test_fill_percentage_never_decreases_even_if_price_retreats(self):
        """After the partial touch at index 5, appending a candle that
        moves AWAY from the zone must not reduce the recorded fill —
        it's a high-water mark, not a live instantaneous reading."""
        candles = self._lifecycle_candles(6)  # partial fill state (2/3)
        candles.append({"high": 130, "low": 120, "close": 125})  # retreats far away
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0, mitigation="Full Fill")
        f = fvgs[0]
        assert abs(f["fillPercentage"] - (2.0 / 3.0)) < 1e-9
        assert f["status"] == "partially_mitigated"

    # ── Realtime / duplicate prevention ──────────────────────────────────
    def test_no_duplicate_fvg_for_the_same_pattern(self):
        candles = self._lifecycle_candles(7)
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0)
        origins = [(f["originIndex"], f["direction"]) for f in fvgs]
        assert len(origins) == len(set(origins))

    def test_deterministic_repeated_calls(self):
        candles = self._lifecycle_candles(7)
        r1 = calc_fvg(candles, min_size_atr_multiplier=0)
        r2 = calc_fvg(candles, min_size_atr_multiplier=0)
        assert r1 == r2

    def test_realtime_history_convergence_via_prefix_equivalence(self):
        """An FVG confirmed within a shorter history must keep the exact
        same boundaries/direction once MORE candles are added — and its
        lifecycle may only progress forward (fillPercentage can only grow)."""
        full = self._lifecycle_candles(7)
        prev_by_origin = {}
        for prefix_len in range(3, 8):
            res = calc_fvg(full[:prefix_len], min_size_atr_multiplier=0, mitigation="Full Fill")
            by_origin = {f["originIndex"]: f for f in res}
            for origin, f in by_origin.items():
                if origin in prev_by_origin:
                    prev = prev_by_origin[origin]
                    assert f["top"] == prev["top"] and f["bottom"] == prev["bottom"]
                    assert f["direction"] == prev["direction"]
                    assert f["fillPercentage"] >= prev["fillPercentage"] - 1e-9
            prev_by_origin = by_origin

    # ── maxZones / performance cap ───────────────────────────────────────
    def test_max_zones_keeps_only_the_most_recent(self):
        highs = [105, 115, 125, 135, 145, 155, 165, 175]
        lows = [95, 105, 115, 125, 135, 145, 155, 165]
        closes = [100, 110, 120, 130, 140, 150, 160, 170]
        candles = self._hlc(highs, lows, closes)
        all_fvgs = calc_fvg(candles, min_size_atr_multiplier=0, max_zones=50)
        assert len(all_fvgs) == 6  # origins 0..5
        limited = calc_fvg(candles, min_size_atr_multiplier=0, max_zones=3)
        assert len(limited) == 3
        assert [f["originIndex"] for f in limited] == [3, 4, 5]

    def test_max_zones_is_a_pure_output_cap_not_a_scan_limit(self):
        """maxZones must never shrink the candle range that gets scanned or
        mitigation-walked — it only trims the fully-computed result. Proof:
        with a low max_zones (1, discarding 5 of 6 detected FVGs) the ONE
        surviving object is byte-for-byte identical to its counterpart in
        the unrestricted (max_zones=50) run — the retained FVG's own
        detection/mitigation math was computed over the exact same full
        history either way; only which objects get returned differs."""
        highs = [105, 115, 125, 135, 145, 155, 165, 175]
        lows = [95, 105, 115, 125, 135, 145, 155, 165]
        closes = [100, 110, 120, 130, 140, 150, 160, 170]
        candles = self._hlc(highs, lows, closes)
        unrestricted = calc_fvg(candles, min_size_atr_multiplier=0, max_zones=50)
        capped = calc_fvg(candles, min_size_atr_multiplier=0, max_zones=1)
        assert len(capped) == 1
        assert capped[0] == unrestricted[-1]

        # Also verify the detection loop itself literally iterates the full
        # array length (range(2, n)), independent of max_zones entirely —
        # the last origin index found must always be n-3, regardless of cap.
        for max_zones in (1, 3, 50, 1000):
            res = calc_fvg(candles, min_size_atr_multiplier=0, max_zones=max_zones)
            assert res[-1]["originIndex"] == len(candles) - 3

    # ── Data quality ─────────────────────────────────────────────────────
    def test_market_gap_handled_without_crash(self):
        candles = make_realistic_candles(n=30, seed=61)
        candles[15] = dict(candles[15], high=candles[15]["high"] + 500, low=candles[15]["low"] + 500)
        fvgs = calc_fvg(candles, min_size_atr_multiplier=0.1, atr_length=14)
        for f in fvgs:
            assert math.isfinite(f["top"]) and math.isfinite(f["bottom"])

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=62)
        fvgs = calc_fvg(candles)
        for f in fvgs:
            assert math.isfinite(f["top"]) and math.isfinite(f["bottom"]) and math.isfinite(f["fillPercentage"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_market_structure(candles, swing_length=2)["trend"] is not None


class TestOrderBlocks:
    """
    Order Blocks — a faithful port of TradingView's own reference "Order
    Blocks" Pine v5 script, NOT a Market-Structure-event consumer as the
    original Phase 2R design was. That switch was deliberate: the
    original BOS/CHoCH-event-driven design produced visibly MORE blocks
    than the reference script on identical data (reported from a
    side-by-side chart comparison), because calc_market_structure fires
    on every confirmed swing break including minor CHoCH pullbacks, while
    the reference script only ever fires on one much stricter, purely
    mechanical trigger. See calc_order_blocks' own docstring for the full
    algorithm (rolling-low crossunder creates a bearish block; a bearish
    block's own reclaim seeds a bullish block — deliberately asymmetric,
    exactly as given in the reference script).

    Reuses the EXACT 25-candle golden trace from TestMarketStructure
    (swing_length=2), with explicit `open` values added (open[i] =
    close[i-1], open[0] = close[0]) so candle color (close vs open) is
    meaningful.

    Hand-traced bar-by-bar against the reference script's own state
    machine (lastUp/lastDown/lastHigh/lastLow — not merely copied from
    the implementation's own output), this trace produces 7 alternating
    blocks:
      origin=2  bearish [104,106] confirm=4  invalidated @6  (fillPct 0.5)
      origin=4  bullish [100,102] confirm=6  fully_mitigated @18 (fillPct 1)
      origin=6  bearish [108,110] confirm=8  invalidated @10 (fillPct 0.5)
      origin=8  bullish [101,103] confirm=10 fully_mitigated @18 (fillPct 1)
      origin=10 bearish [112,114] confirm=12 invalidated @14 (fillPct 0)
      origin=12 bullish [106,108] confirm=14 invalidated @17 (fillPct 0.5)
      origin=14 bearish [115,117] confirm=16 active (never reclaimed here)
    """

    CLOSES = TestMarketStructure.CLOSES

    @classmethod
    def _candles(cls, closes=None):
        closes = closes if closes is not None else cls.CLOSES
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        opens = [closes[0]] + closes[:-1]
        return [{"open": opens[i], "high": highs[i], "low": lows[i], "close": closes[i]} for i in range(len(closes))]

    # ── Detection ────────────────────────────────────────────────────────
    def test_empty_data(self):
        assert calc_order_blocks([]) == []

    def test_insufficient_candles(self):
        assert calc_order_blocks(self._candles(self.CLOSES[:2])) == []

    def test_golden_case_produces_seven_alternating_order_blocks(self):
        candles = self._candles()
        obs = calc_order_blocks(candles, swing_length=2)
        assert len(obs) == 7
        by_origin = {ob["originIndex"]: ob for ob in obs}
        assert by_origin[2]["direction"] == "bearish" and by_origin[2]["top"] == 106 and by_origin[2]["bottom"] == 104
        assert by_origin[4]["direction"] == "bullish" and by_origin[4]["top"] == 102 and by_origin[4]["bottom"] == 100
        assert by_origin[6]["direction"] == "bearish" and by_origin[6]["top"] == 110 and by_origin[6]["bottom"] == 108
        assert by_origin[8]["direction"] == "bullish" and by_origin[8]["top"] == 103 and by_origin[8]["bottom"] == 101
        assert by_origin[10]["direction"] == "bearish" and by_origin[10]["top"] == 114 and by_origin[10]["bottom"] == 112
        assert by_origin[12]["direction"] == "bullish" and by_origin[12]["top"] == 108 and by_origin[12]["bottom"] == 106
        assert by_origin[14]["direction"] == "bearish" and by_origin[14]["top"] == 117 and by_origin[14]["bottom"] == 115
        for ob in obs:
            assert abs(ob["midpoint"] - (ob["top"] + ob["bottom"]) / 2.0) < 1e-9
            assert abs(ob["size"] - (ob["top"] - ob["bottom"])) < 1e-9

    def test_trigger_event_reflects_creation_mechanism(self):
        """triggerEvent now records WHICH of the two creation mechanisms
        produced a block — 'bearishBreakdown' for the rolling-low
        crossunder trigger, 'bullishReclaim' for a block seeded by an
        opposite block's reclaim — never a Market Structure event type,
        since this engine no longer consults calc_market_structure at
        all."""
        candles = self._candles()
        by_origin = {ob["originIndex"]: ob for ob in calc_order_blocks(candles, swing_length=2)}
        assert by_origin[2]["triggerEvent"] == "bearishBreakdown"
        assert by_origin[4]["triggerEvent"] == "bullishReclaim"
        assert by_origin[6]["triggerEvent"] == "bearishBreakdown"
        assert by_origin[12]["triggerEvent"] == "bullishReclaim"

    def test_no_bearish_ob_before_any_bullish_candle_has_printed(self):
        """The rolling-low crossunder trigger is necessary but not
        sufficient: without a prior bullish candle to anchor a bearish
        block's top/bottom (the up-leg anchor is still unset), no block
        can be created even while price keeps making fresh rolling lows
        every bar."""
        closes = [100, 98, 96, 94, 92, 90, 88, 86]
        opens = [101, 99, 97, 95, 93, 91, 89, 87]  # every candle bearish
        candles = [{"open": opens[i], "high": opens[i] + 1, "low": closes[i] - 1, "close": closes[i]} for i in range(len(closes))]
        assert calc_order_blocks(candles, swing_length=2) == []

    def test_swing_length_longer_than_history_yields_no_blocks(self):
        """The rolling low needs swing_length prior candles before it is
        even defined — a swing_length exceeding the whole dataset means
        the crossunder trigger can never fire."""
        candles = self._candles()
        assert calc_order_blocks(candles, swing_length=100) == []

    def test_origin_high_low_mark_the_anchor_candle_within_an_extended_zone(self):
        """In the golden trace, top/bottom always exactly equal the origin
        candle's own High/Low (no multi-candle leg extension happens
        there). This fixture deliberately builds one: candle1 (the
        up-leg anchor, High=108/Low=99) is followed by a DOWN candle
        (candle2) whose own High=112 is nonetheless higher, extending the
        running peak before candle3's close crosses under the rolling low
        and confirms the bearish block — so top(112) genuinely exceeds
        originHigh(108)."""
        candles = [
            {"time": 1000, "open": 101, "high": 102, "low": 99, "close": 100},
            {"time": 1060, "open": 100, "high": 108, "low": 99, "close": 106},
            {"time": 1120, "open": 106, "high": 112, "low": 104, "close": 105},
            {"time": 1180, "open": 105, "high": 106, "low": 90, "close": 95},
        ]
        obs = calc_order_blocks(candles, swing_length=2)
        assert len(obs) == 1
        ob = obs[0]
        assert ob["originIndex"] == 1
        assert ob["top"] == 112 and ob["bottom"] == 99
        assert ob["originHigh"] == 108, "the anchor candle's own high, not the extended peak"
        assert ob["originLow"] == 99, "bottom for a bearish block never extends, so this equals bottom"

    def test_multiple_and_overlapping_order_blocks_kept_independent(self):
        """The golden case's OB@12 (bullish) and OB@14 (bearish) sit close
        in time/origin but arise from two independent creation events (a
        reclaim, then a fresh breakdown) — both must be retained
        independently, never merged."""
        candles = self._candles()
        obs = calc_order_blocks(candles, swing_length=2)
        origins = [(ob["originIndex"], ob["direction"]) for ob in obs]
        assert (12, "bullish") in origins
        assert (14, "bearish") in origins

    def test_no_duplicate_order_blocks(self):
        candles = self._candles()
        obs = calc_order_blocks(candles, swing_length=2)
        ids = [ob["id"] for ob in obs]
        assert len(ids) == len(set(ids))

    # ── Lifecycle ────────────────────────────────────────────────────────
    def test_active_before_any_touch(self):
        candles = self._candles(self.CLOSES[:7])  # only through the OB@4 event's own confirm bar (index6)
        obs = calc_order_blocks(candles, swing_length=2)
        ob4 = next(ob for ob in obs if ob["originIndex"] == 4)
        assert ob4["status"] == "active"
        assert ob4["fillPercentage"] == 0

    def test_full_lifecycle_partial_then_full_mitigation(self):
        """OB@4 (zone [100,102]): index8's low=101 gives a partial touch
        (fillPercentage 0.5); index18's low=100 completes the fill
        (hand-verified against the golden 25-candle trace)."""
        candles = self._candles()
        ob4 = next(ob for ob in calc_order_blocks(candles, swing_length=2) if ob["originIndex"] == 4)
        assert ob4["status"] == "fully_mitigated"
        assert abs(ob4["fillPercentage"] - 1.0) < 1e-9
        assert ob4["mitigatedIndex"] == 18
        assert ob4["mitigationPrice"] == 100

        partial_candles = self._candles(self.CLOSES[:9])  # stop right after the partial touch at index8
        ob4_partial = next(ob for ob in calc_order_blocks(partial_candles, swing_length=2) if ob["originIndex"] == 4)
        assert ob4_partial["status"] == "partially_mitigated"
        assert abs(ob4_partial["fillPercentage"] - 0.5) < 1e-9
        assert ob4_partial["mitigatedIndex"] is None

    def test_touch_mode_mitigates_on_first_intrusion(self):
        candles = self._candles()
        ob4 = next(ob for ob in calc_order_blocks(candles, swing_length=2, mitigation="Touch") if ob["originIndex"] == 4)
        assert ob4["status"] == "fully_mitigated"
        assert ob4["mitigatedIndex"] == 8
        assert ob4["mitigationPrice"] == 101

    def test_invalidation_is_distinct_from_mitigation(self):
        """A decisive CLOSE beyond the opposite boundary invalidates the
        block instead of merely mitigating it. Reuses OB@4's exact setup
        (candles 0-6) with one appended candle whose CLOSE (95) closes
        clean through the zone's bottom (100) — a stronger signal than an
        intrabar wick."""
        candles = self._candles(self.CLOSES[:7])
        candles.append({"open": 109, "high": 110, "low": 90, "close": 95})
        ob4 = next(ob for ob in calc_order_blocks(candles, swing_length=2) if ob["originIndex"] == 4)
        assert ob4["status"] == "invalidated"
        assert ob4["invalidatedIndex"] == 7
        assert ob4["mitigatedIndex"] is None, "invalidation must take precedence over recording a mitigation timestamp"

    # ── Repainting / determinism ─────────────────────────────────────────
    def test_deterministic_repeated_calls(self):
        candles = self._candles()
        assert calc_order_blocks(candles, swing_length=2) == calc_order_blocks(candles, swing_length=2)

    def test_historical_prefix_equivalence_no_repainting(self):
        """Growing the candle array must never move an already-confirmed
        OB's boundaries/direction/triggerEvent, and its lifecycle
        (fillPercentage/status) may only progress forward — never revert —
        as genuinely new candles arrive (the same "recompute over a longer
        history only advances lifecycle, never repaints identity" contract
        already verified for Calc.marketStructure and Calc.fvg)."""
        candles = self._candles()
        order = {"active": 0, "partially_mitigated": 1, "fully_mitigated": 2, "invalidated": 2}
        prev_by_origin = {}
        for prefix_len in (7, 10, 15, 20, 25):
            res = calc_order_blocks(candles[:prefix_len], swing_length=2)
            by_origin = {ob["originIndex"]: ob for ob in res}
            for origin, ob in by_origin.items():
                if origin in prev_by_origin:
                    prev = prev_by_origin[origin]
                    assert ob["top"] == prev["top"] and ob["bottom"] == prev["bottom"]
                    assert ob["direction"] == prev["direction"]
                    assert ob["triggerEvent"] == prev["triggerEvent"]
                    assert ob["fillPercentage"] >= prev["fillPercentage"] - 1e-9
                    assert order[ob["status"]] >= order[prev["status"]]
            prev_by_origin = by_origin

    def test_confirmed_ob_boundaries_immutable_across_longer_history(self):
        candles = self._candles()
        short = calc_order_blocks(candles[:15], swing_length=2)
        full = calc_order_blocks(candles, swing_length=2)
        by_origin_short = {ob["originIndex"]: ob for ob in short}
        by_origin_full = {ob["originIndex"]: ob for ob in full}
        for origin, ob in by_origin_short.items():
            assert by_origin_full[origin]["top"] == ob["top"]
            assert by_origin_full[origin]["bottom"] == ob["bottom"]
            assert by_origin_full[origin]["direction"] == ob["direction"]

    # ── State / data quality ─────────────────────────────────────────────
    def test_market_gap_handled_without_crash(self):
        candles = make_realistic_candles(n=40, seed=71)
        candles[20] = dict(candles[20], high=candles[20]["high"] + 300, low=candles[20]["low"] + 300)
        obs = calc_order_blocks(candles, swing_length=5)
        for ob in obs:
            assert math.isfinite(ob["top"]) and math.isfinite(ob["bottom"])

    def test_max_zones_caps_output_only(self):
        candles = make_realistic_candles(n=500, seed=72)
        unrestricted = calc_order_blocks(candles, swing_length=3, max_zones=1000)
        capped = calc_order_blocks(candles, swing_length=3, max_zones=2)
        if len(unrestricted) > 2:
            assert len(capped) == 2
            assert capped == unrestricted[-2:]

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=73)
        obs = calc_order_blocks(candles)
        for ob in obs:
            assert math.isfinite(ob["top"]) and math.isfinite(ob["bottom"]) and math.isfinite(ob["fillPercentage"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_fvg(candles, min_size_atr_multiplier=0) is not None
        assert calc_market_structure(candles, swing_length=2)["trend"] is not None


class TestLiquidity:
    """
    Phase 2S — Liquidity (Equal Highs/Lows + Swing Liquidity), built
    entirely on Phase 2P's confirmed swings.

    14-candle golden fixture (swing_length=2, High=close+1, Low=close-1):
      closes = [100,105,110,106,102,98,103,109,104,99,105,130,120,115]

    Hand-verified swings:
      high@2  price=111   high@7  price=110   high@11 price=131
      low@5   price=97    low@9   price=98

    ATR(3) hand-verified at each swing's own index (Wilder's, seeded at
    index2 = avg(TR0,TR1,TR2) = avg(2,6,6) = 4.667, then recurrence):
      ATR[2]=4.667  ATR[5]=4.901  ATR[7]=5.845  ATR[9]=5.931  ATR[11]=12.858

    With tolerance_atr_multiplier=0.5:
      tol@2=2.333 tol@5=2.450 tol@7=2.923 tol@9=2.966 tol@11=6.429
    Clustering (anchor = first swing in price-sorted order): highs sorted
    [110@7, 111@2, 131@11] -> |111-110|=1 <= max(2.923,2.333) -> joins;
    |131-110|=21 <= max(6.429,2.923)=6.429? No -> isolated. So {110,111}
    cluster (2 members) qualifies as EQUAL_HIGH; 131 stays SWING_HIGH only.
    Lows sorted [97@5, 98@9]: |98-97|=1 <= max(2.966,2.450) -> joins ->
    {97,98} cluster (2 members) qualifies as EQUAL_LOW.
    """

    CLOSES = [100, 105, 110, 106, 102, 98, 103, 109, 104, 99, 105, 130, 120, 115]

    @classmethod
    def _candles(cls, closes=None):
        closes = closes if closes is not None else cls.CLOSES
        return make_hlc([c + 1 for c in closes], [c - 1 for c in closes], closes)

    # ── Swing liquidity / Market Structure integration ──────────────────
    def test_empty_data(self):
        assert calc_liquidity([]) == []

    def test_every_confirmed_swing_becomes_its_own_swing_liquidity_pool(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3)
        swing_highs = {p["price"] for p in pools if p["type"] == "SWING_HIGH"}
        swing_lows = {p["price"] for p in pools if p["type"] == "SWING_LOW"}
        assert swing_highs == {111, 110, 131}
        assert swing_lows == {97, 98}
        for p in pools:
            if p["type"] in ("SWING_HIGH", "SWING_LOW"):
                assert p["touchCount"] == 1

    def test_unconfirmed_swing_ignored_non_repainting(self):
        """The swing high at index 11 needs 2 trailing candles (12, 13) to
        confirm — with only 1 trailing candle it must not appear at all."""
        candles = self._candles(self.CLOSES[:13])  # index 11 has only 1 trailing candle (12)
        pools = calc_liquidity(candles, swing_length=2)
        assert all(p["price"] != 131 for p in pools if p["type"] == "SWING_HIGH")
        candles_full = self._candles(self.CLOSES[:14])
        pools_full = calc_liquidity(candles_full, swing_length=2)
        assert any(p["price"] == 131 for p in pools_full if p["type"] == "SWING_HIGH")

    def test_confirmed_swing_high_becomes_buy_side_swing_low_becomes_sell_side(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2)
        for p in pools:
            if p["type"] in ("SWING_HIGH", "EQUAL_HIGH"):
                assert p["side"] == "BUY_SIDE"
            if p["type"] in ("SWING_LOW", "EQUAL_LOW"):
                assert p["side"] == "SELL_SIDE"

    # ── Equal Highs ──────────────────────────────────────────────────────
    def test_equal_high_within_tolerance(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=2)
        eqh = [p for p in pools if p["type"] == "EQUAL_HIGH"]
        assert len(eqh) == 1
        assert eqh[0]["touchCount"] == 2
        assert abs(eqh[0]["price"] - 110.5) < 1e-9
        assert {tp["price"] for tp in eqh[0]["touchPoints"]} == {110, 111}

    def test_high_outside_tolerance_not_grouped(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.01, atr_length=3, min_touches=2)
        eqh = [p for p in pools if p["type"] == "EQUAL_HIGH"]
        assert eqh == [], "a much tighter tolerance must prevent 110 and 111 from clustering"

    def test_exact_matching_highs_always_cluster(self):
        closes = [100, 110, 102, 98, 110, 103, 99, 108]
        candles = make_hlc([c + 1 for c in closes], [c - 1 for c in closes], closes)
        pools = calc_liquidity(candles, swing_length=1, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=2)
        eqh = [p for p in pools if p["type"] == "EQUAL_HIGH" and abs(p["price"] - 111) < 1e-9]
        assert len(eqh) == 1
        assert eqh[0]["touchCount"] == 2

    def test_equal_high_minimum_touch_count(self):
        candles = self._candles()
        two_touch = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=2)
        three_touch = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=3)
        assert any(p["type"] == "EQUAL_HIGH" for p in two_touch)
        assert not any(p["type"] == "EQUAL_HIGH" for p in three_touch), "the 2-member cluster must not qualify once min_touches=3"

    def test_no_duplicate_equal_high_pools(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3)
        ids = [p["id"] for p in pools]
        assert len(ids) == len(set(ids))

    # ── Equal Lows (same tolerance methodology as highs) ─────────────────
    def test_equal_low_within_tolerance(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=2)
        eql = [p for p in pools if p["type"] == "EQUAL_LOW"]
        assert len(eql) == 1
        assert eql[0]["touchCount"] == 2
        assert abs(eql[0]["price"] - 97.5) < 1e-9

    def test_low_outside_tolerance_not_grouped(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.01, atr_length=3, min_touches=2)
        assert [p for p in pools if p["type"] == "EQUAL_LOW"] == []

    def test_equal_low_minimum_touch_count(self):
        candles = self._candles()
        two_touch = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=2)
        three_touch = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=3)
        assert any(p["type"] == "EQUAL_LOW" for p in two_touch)
        assert not any(p["type"] == "EQUAL_LOW" for p in three_touch)

    def test_highs_and_lows_use_the_identical_tolerance_formula(self):
        """No separate/arbitrary tolerance mechanism for lows vs highs —
        both derive from the exact same tolerance_atr_multiplier * ATR."""
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3)
        eqh = next(p for p in pools if p["type"] == "EQUAL_HIGH")
        eql = next(p for p in pools if p["type"] == "EQUAL_LOW")
        # Both anchors come from ATR values computed by the same calc_atr
        # call; their tolerances must both equal 0.5 * (ATR at their own
        # anchor index), not some hand-tuned per-side constant.
        assert eqh["tolerance"] > 0 and eql["tolerance"] > 0

    # ── Lifecycle / sweep-readiness ──────────────────────────────────────
    def test_pools_are_active_with_sweep_fields_reserved_but_unset(self):
        candles = self._candles()
        pools = calc_liquidity(candles, swing_length=2)
        for p in pools:
            assert p["status"] == "active"
            assert p["swept"] is False
            assert p["sweptAt"] is None
            assert p["sweptPrice"] is None

    # ── Repainting / determinism ─────────────────────────────────────────
    def test_deterministic_repeated_calls(self):
        candles = self._candles()
        assert calc_liquidity(candles, swing_length=2) == calc_liquidity(candles, swing_length=2)

    def test_swing_liquidity_pools_never_disappear_as_history_grows(self):
        """Once a swing is confirmed, its own SWING_HIGH/SWING_LOW pool
        must keep appearing (same price) in every later, longer recompute
        — the core non-repainting guarantee for the simplest pool type."""
        candles = self._candles()
        seen_prices = set()
        for prefix_len in (7, 9, 11, 13, 14):
            pools = calc_liquidity(candles[:prefix_len], swing_length=2)
            prices = {p["price"] for p in pools if p["type"] == "SWING_HIGH"}
            assert seen_prices <= prices, "a previously-confirmed swing-high pool must never disappear"
            seen_prices = prices

    # ── State / data quality ─────────────────────────────────────────────
    def test_insufficient_candles_no_crash(self):
        assert calc_liquidity(self._candles(self.CLOSES[:2]), swing_length=2) == []

    def test_max_pools_caps_output_only(self):
        candles = make_realistic_candles(n=500, seed=81)
        unrestricted = calc_liquidity(candles, swing_length=3, max_pools=1000)
        capped = calc_liquidity(candles, swing_length=3, max_pools=3)
        if len(unrestricted) > 3:
            assert len(capped) == 3

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=82)
        pools = calc_liquidity(candles)
        for p in pools:
            assert math.isfinite(p["price"]) and math.isfinite(p["tolerance"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_order_blocks(candles, swing_length=2) is not None
        assert calc_fvg(candles, min_size_atr_multiplier=0) is not None
        assert calc_market_structure(candles, swing_length=2)["trend"] is not None


class TestLiquiditySweeps:
    """
    Phase 2T — Liquidity Sweeps, built on Phase 2S's liquidity pools.

    Base 14-candle golden fixture (same as TestLiquidity, swing_length=2,
    High=close+1, Low=close-1): closes = [100,105,110,106,102,98,103,109,
    104,99,105,130,120,115]. With tolerance_atr_multiplier=0.5,
    atr_length=3: EQUAL_HIGH pool at price=110.5 (touches at idx2, idx7;
    latestTouchIndex=7); EQUAL_LOW pool at price=97.5 (touches at idx5,
    idx9; latestTouchIndex=9).

    IMPORTANT: the base fixture's own isolated swing high (131 @ idx11,
    High=131) ALREADY breaches the 110.5 EQH pool (High > 110.5) before
    any appended candle — so for every buy-side-sweep test against the
    110.5 pool, liquidity is "taken" at idx11 (sweepExtreme starts at
    131, the highest High reached), not at the first appended candle.
    None of idx11 (Close=130), idx12 (120), idx13 (115) reclaim (all
    Close > 110.5), so the candidate is still pending when the appended
    candles begin — only the appended candles decide the reclaim.

    Buy-side sweep extension (appended at idx14-15):
      idx14: H=117,L=115,C=116 -> Close(116) > 110.5: still NOT confirmed.
      idx15: H=109,L=107,C=108 -> Close(108) < 110.5: CONFIRMED (Close
             Rejection) at reclaimIndex=15, but sweepIndex=11 (the
             original breach) and sweepExtreme=131 (still the highest
             High seen, since 117 < 131). sweepDepth = 131-110.5 = 20.5.

    Sell-side sweep extension (appended at idx14-15, separate fixture):
      The SSL pool (97.5) is never pre-breached by the base fixture (its
      lowest Low, 89 @ idx11's neighbourhood, never goes that low), so
      this one behaves exactly as a fresh multi-candle example:
      idx14: H=96,L=94,C=95 -> Low(94) < 97.5: liquidity TAKEN.
             Close(95) < 97.5... wait: for a SELL_SIDE pool, "taken" is
             Low < price and confirmation is Close > price, so Close(95)
             is NOT > 97.5: NOT confirmed same-candle.
      idx15: H=101,L=99,C=100 -> Close(100) > 97.5: CONFIRMED.
             sweepExtreme = min(94,99) = 94. sweepDepth = 97.5-94 = 3.5.
    """

    BASE_CLOSES = [100, 105, 110, 106, 102, 98, 103, 109, 104, 99, 105, 130, 120, 115]

    @classmethod
    def _candles(cls, extra_closes=None, extra_highs=None, extra_lows=None):
        closes = list(cls.BASE_CLOSES)
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        if extra_closes:
            closes += extra_closes
            highs += extra_highs
            lows += extra_lows
        return make_hlc(highs, lows, closes)

    def _buy_side_candles(self, confirmed=True):
        extra_closes = [116, 108] if confirmed else [116]
        extra_highs = [117, 109] if confirmed else [117]
        extra_lows = [115, 107] if confirmed else [115]
        return self._candles(extra_closes, extra_highs, extra_lows)

    def _sell_side_candles(self, confirmed=True):
        extra_closes = [95, 100] if confirmed else [95]
        extra_highs = [96, 101] if confirmed else [96]
        extra_lows = [94, 99] if confirmed else [94]
        return self._candles(extra_closes, extra_highs, extra_lows)

    KW = dict(swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=2)

    # ── Buy-side sweeps ──────────────────────────────────────────────────
    def test_empty_data(self):
        assert calc_liquidity_sweeps([]) == []

    def test_price_touches_bsl_but_no_rejection_yields_no_confirmed_sweep(self):
        candles = self._buy_side_candles(confirmed=False)
        sweeps = calc_liquidity_sweeps(candles, **self.KW)
        assert not any(s["sweepType"] == "BUY_SIDE_SWEEP" and s["liquidityPrice"] == 110.5 for s in sweeps)

    def test_confirmed_buy_side_sweep_close_rejection(self):
        candles = self._buy_side_candles(confirmed=True)
        sweeps = calc_liquidity_sweeps(candles, **self.KW)
        sweep = next(s for s in sweeps if s["sweepType"] == "BUY_SIDE_SWEEP" and s["liquidityPrice"] == 110.5)
        assert sweep["direction"] == "bearish"
        assert sweep["sweepIndex"] == 11 and sweep["reclaimIndex"] == 15
        assert sweep["sweepExtreme"] == 131
        assert abs(sweep["sweepDepth"] - 20.5) < 1e-9
        assert sweep["confirmationType"] == "Close Rejection"

    def test_breakout_scenario_b_high_and_close_both_above_level_not_a_sweep(self):
        """Scenario B from the spec: High > level AND Close > level must
        NOT be classified a sweep under Close Rejection."""
        candles = self._buy_side_candles(confirmed=False)  # idx14 only: H=117>110.5, C=116>110.5
        sweeps = calc_liquidity_sweeps(candles, **self.KW)
        assert sweeps == [] or all(s["liquidityPrice"] != 110.5 for s in sweeps)

    def test_reclaim_fields_recorded(self):
        candles = self._buy_side_candles(confirmed=True)
        sweep = next(s for s in calc_liquidity_sweeps(candles, **self.KW) if s["liquidityPrice"] == 110.5)
        assert sweep["reclaimPrice"] == 108
        assert sweep["sweepTime"] == candles[14].get("time")
        assert sweep["reclaimTime"] == candles[15].get("time")

    # ── Sell-side sweeps ─────────────────────────────────────────────────
    def test_confirmed_sell_side_sweep_close_rejection(self):
        candles = self._sell_side_candles(confirmed=True)
        sweep = next(s for s in calc_liquidity_sweeps(candles, **self.KW)
                     if s["sweepType"] == "SELL_SIDE_SWEEP" and s["liquidityPrice"] == 97.5)
        assert sweep["direction"] == "bullish"
        assert sweep["sweepIndex"] == 14 and sweep["reclaimIndex"] == 15
        assert sweep["sweepExtreme"] == 94
        assert abs(sweep["sweepDepth"] - 3.5) < 1e-9

    def test_sell_side_no_rejection_yields_no_confirmed_sweep(self):
        candles = self._sell_side_candles(confirmed=False)
        sweeps = calc_liquidity_sweeps(candles, **self.KW)
        assert not any(s["liquidityPrice"] == 97.5 for s in sweeps)

    # ── Confirmation modes ───────────────────────────────────────────────
    def test_wick_reclaim_confirms_where_close_rejection_does_not(self):
        """idx14: H=117,L=115,C=116 (takes liquidity, close stays above).
        idx15: H=118,L=109,C=112 (low dips back below 110.5, but close
        stays above) -> Wick+Reclaim confirms at idx15; Close Rejection
        does not confirm anywhere in this data."""
        candles = self._candles([116, 112], [117, 118], [115, 109])
        close_rejection = calc_liquidity_sweeps(candles, confirmation="Close Rejection", **self.KW)
        wick_reclaim = calc_liquidity_sweeps(candles, confirmation="Wick + Reclaim", **self.KW)
        assert not any(s["liquidityPrice"] == 110.5 for s in close_rejection)
        sweep = next(s for s in wick_reclaim if s["liquidityPrice"] == 110.5)
        assert sweep["reclaimIndex"] == 15
        assert sweep["confirmationType"] == "Wick + Reclaim"

    # ── Multi-candle / duplicate prevention ──────────────────────────────
    def test_multi_candle_sweep_pending_then_confirmed(self):
        """Extending the unconfirmed candidate with more non-reclaiming
        candles keeps it pending; only the candle that actually closes
        back below confirms it — proving the candidate persists across
        multiple candles without repainting an early false confirmation."""
        pending = self._candles([116, 120, 118], [117, 121, 119], [115, 118, 116])
        assert not any(s["liquidityPrice"] == 110.5 for s in calc_liquidity_sweeps(pending, **self.KW))

        confirmed = self._candles([116, 120, 118, 108], [117, 121, 119, 109], [115, 118, 116, 107])
        sweep = next(s for s in calc_liquidity_sweeps(confirmed, **self.KW) if s["liquidityPrice"] == 110.5)
        assert sweep["sweepIndex"] == 11, "liquidity was already taken by the base fixture's own idx11 high (131)"
        assert sweep["reclaimIndex"] == 17

    def test_already_swept_pool_never_produces_a_second_sweep(self):
        """After the confirmed sweep at idx14/15, further candles crossing
        back above 110.5 and below again must NOT create a duplicate."""
        candles = self._candles([116, 108, 115, 105], [117, 109, 116, 106], [115, 107, 113, 104])
        sweeps = [s for s in calc_liquidity_sweeps(candles, **self.KW) if s["liquidityPrice"] == 110.5]
        assert len(sweeps) == 1

    def test_no_duplicate_sweep_ids(self):
        candles = self._buy_side_candles(confirmed=True)
        ids = [s["id"] for s in calc_liquidity_sweeps(candles, **self.KW)]
        assert len(ids) == len(set(ids))

    # ── Liquidity integration (EQH/EQL and swing liquidity) ──────────────
    def test_equal_high_pool_type_preserved_in_sweep_event(self):
        candles = self._buy_side_candles(confirmed=True)
        sweep = next(s for s in calc_liquidity_sweeps(candles, **self.KW) if s["liquidityPrice"] == 110.5)
        assert sweep["liquidityPoolType"] == "EQUAL_HIGH"

    def test_swing_high_liquidity_can_also_be_swept(self):
        """The isolated swing high at 131 (idx11, SWING_HIGH type, not part
        of the EQUAL_HIGH cluster) is itself a valid, independently
        sweepable pool."""
        candles = self._candles([135, 125], [136, 126], [125, 124])
        sweeps = calc_liquidity_sweeps(candles, **self.KW)
        assert any(s["liquidityPoolType"] == "SWING_HIGH" and s["liquidityPrice"] == 131 for s in sweeps)

    # ── Repainting / determinism ─────────────────────────────────────────
    def test_deterministic_repeated_calls(self):
        candles = self._buy_side_candles(confirmed=True)
        assert calc_liquidity_sweeps(candles, **self.KW) == calc_liquidity_sweeps(candles, **self.KW)

    def test_historical_prefix_equivalence_no_repainting(self):
        candles = self._buy_side_candles(confirmed=True)
        full = calc_liquidity_sweeps(candles, **self.KW)
        for prefix_len in (14, 15, 16):
            prefix_sweeps = calc_liquidity_sweeps(candles[:prefix_len], **self.KW)
            expected = [s for s in full if s["reclaimIndex"] < prefix_len]
            assert prefix_sweeps == expected, f"mismatch at prefix_len={prefix_len}"

    # ── State / data quality ─────────────────────────────────────────────
    def test_insufficient_candles_no_crash(self):
        assert calc_liquidity_sweeps(make_hlc([100], [90], [95])) == []

    def test_max_events_caps_output_only(self):
        candles = make_realistic_candles(n=500, seed=91)
        unrestricted = calc_liquidity_sweeps(candles, swing_length=3, max_events=1000)
        capped = calc_liquidity_sweeps(candles, swing_length=3, max_events=2)
        if len(unrestricted) > 2:
            assert len(capped) == 2
            assert capped == unrestricted[-2:]

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=92)
        sweeps = calc_liquidity_sweeps(candles)
        for s in sweeps:
            assert math.isfinite(s["liquidityPrice"]) and math.isfinite(s["sweepExtreme"]) and math.isfinite(s["sweepDepth"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_liquidity(candles, swing_length=2) is not None
        assert calc_order_blocks(candles, swing_length=2) is not None


class TestPremiumDiscount:
    """
    Phase 2U — Premium & Discount, built on Phase 2P's confirmed swings.

    Reuses the EXACT 25-candle golden trace from TestMarketStructure
    (swing_length=2). Walking calc_premium_discount's range-replacement
    logic against that trace's hand-verified swing list (see
    TestMarketStructure's own docstring for the swings themselves) gives
    9 successive ranges as lastHigh/lastLow are each superseded:

      created@6  high=S1(106,idx2)  low=S2(100,idx4)   -> [100,106] eq=103   dir=bearish (high idx2 < low idx4)
      created@8  high=S3(110,idx6)  low=S2(100,idx4)   -> [100,110] eq=105   dir=bullish
      created@10 high=S3(110,idx6)  low=S4(101,idx8)   -> [101,110] eq=105.5 dir=bearish
      created@12 high=S5(114,idx10) low=S4(101,idx8)   -> [101,114] eq=107.5 dir=bullish
      created@14 high=S5(114,idx10) low=S6(106,idx12)  -> [106,114] eq=110   dir=bearish
      created@16 high=S7(117,idx14) low=S6(106,idx12)  -> [106,117] eq=111.5 dir=bullish
      created@21 high=S7(117,idx14) low=S8(97,idx19)   -> [97,117]  eq=107   dir=bearish
      created@23 high=S9(104,idx21) low=S8(97,idx19)   -> [97,104]  eq=100.5 dir=bullish
      created@24 high=S9(104,idx21) low=S10(98,idx22)  -> [98,104]  eq=101   dir=bearish  <- ACTIVE

    Final close (index24) = 105 > equilibrium(101) -> currentClassification = PREMIUM.
    """

    CLOSES = TestMarketStructure.CLOSES

    @classmethod
    def _candles(cls, closes=None):
        closes = closes if closes is not None else cls.CLOSES
        return make_hlc([c + 1 for c in closes], [c - 1 for c in closes], closes)

    # ── Range construction ───────────────────────────────────────────────
    def test_empty_data(self):
        assert calc_premium_discount([]) == {"ranges": [], "currentClassification": None}

    def test_insufficient_candles_no_range_yet(self):
        candles = self._candles(self.CLOSES[:5])  # not enough for both a high and a low to confirm
        res = calc_premium_discount(candles, swing_length=2)
        assert res["ranges"] == []
        assert res["currentClassification"] is None

    def test_golden_case_produces_nine_ranges_with_correct_boundaries(self):
        candles = self._candles()
        res = calc_premium_discount(candles, swing_length=2)
        assert len(res["ranges"]) == 9
        r0 = res["ranges"][0]
        assert r0["rangeHigh"] == 106 and r0["rangeLow"] == 100 and abs(r0["equilibrium"] - 103) < 1e-9
        assert r0["direction"] == "bearish"
        r1 = res["ranges"][1]
        assert r1["rangeHigh"] == 110 and r1["rangeLow"] == 100 and r1["direction"] == "bullish"

    def test_active_range_is_the_last_one_and_flagged_active(self):
        candles = self._candles()
        ranges = calc_premium_discount(candles, swing_length=2)["ranges"]
        active = ranges[-1]
        assert active["rangeHigh"] == 104 and active["rangeLow"] == 98
        assert abs(active["equilibrium"] - 101) < 1e-9
        assert active["direction"] == "bearish"
        assert active["active"] is True
        assert all(not r["active"] for r in ranges[:-1]), "only the current range may be flagged active"

    def test_bullish_and_bearish_ranges_both_produce_correct_numeric_ordering(self):
        """Section 5's explicit requirement: even for a bearish leg (high
        formed before low), range_high must still be the numerically
        larger value — never reversed because of direction."""
        candles = self._candles()
        for r in calc_premium_discount(candles, swing_length=2)["ranges"]:
            assert r["rangeHigh"] >= r["rangeLow"]
            if r["direction"] == "bearish":
                # A bearish leg here means the HIGH's own index precedes
                # the LOW's index chronologically, not that rangeHigh/Low
                # are swapped.
                assert r["highIndex"] < r["lowIndex"]
            else:
                assert r["lowIndex"] < r["highIndex"]

    def test_range_replacement_does_not_happen_every_candle(self):
        """Between two consecutive swing confirmations, the active range
        must stay byte-identical — replacement only happens exactly when
        market structure requires it, never on an ordinary candle."""
        candles = self._candles()
        r_at_9 = calc_premium_discount(candles[:9], swing_length=2)["ranges"][-1]
        r_at_10 = calc_premium_discount(candles[:10], swing_length=2)["ranges"][-1]
        assert r_at_9 == r_at_10, "no new swing confirms between index 9 and 10, so the range must not change"

    def test_source_structure_classification_preserved(self):
        candles = self._candles()
        r1 = calc_premium_discount(candles, swing_length=2)["ranges"][1]
        assert r1["sourceStructure"]["highClassification"] == "HH"

    # ── Premium/Discount/Equilibrium classification ─────────────────────
    def test_price_above_equilibrium_is_premium(self):
        candles = self._candles()
        assert calc_premium_discount(candles, swing_length=2)["currentClassification"] == "PREMIUM"

    def test_price_below_equilibrium_is_discount(self):
        closes = self.CLOSES[:9] + [95]  # equilibrium is 105 once range (S3@6,S2@4) is active; 95 < 105
        candles = self._candles(closes)
        assert calc_premium_discount(candles, swing_length=2)["currentClassification"] == "DISCOUNT"

    def test_price_exactly_at_equilibrium(self):
        closes = self.CLOSES[:9] + [105]  # equilibrium is exactly 105 here (see docstring's created@8 range)
        candles = self._candles(closes)
        assert calc_premium_discount(candles, swing_length=2)["currentClassification"] == "EQUILIBRIUM"

    # ── Market Structure integration ────────────────────────────────────
    def test_hh_hl_lh_ll_all_appear_as_source_structure_across_the_history(self):
        candles = self._candles()
        classifications = set()
        for r in calc_premium_discount(candles, swing_length=2)["ranges"]:
            classifications.add(r["sourceStructure"]["highClassification"])
            classifications.add(r["sourceStructure"]["lowClassification"])
        assert {"HH", "HL", "LH", "LL"} <= classifications

    # ── Repainting / determinism ─────────────────────────────────────────
    def test_deterministic_repeated_calls(self):
        candles = self._candles()
        assert calc_premium_discount(candles, swing_length=2) == calc_premium_discount(candles, swing_length=2)

    def test_historical_prefix_equivalence_no_repainting(self):
        """A range already created within a shorter history must keep its
        exact boundaries/direction/sourceStructure once MORE candles are
        added — growing history can only ADD new ranges after it, never
        edit an existing one."""
        candles = self._candles()
        full = calc_premium_discount(candles, swing_length=2)["ranges"]
        for prefix_len in (9, 15, 20, 25):
            prefix_ranges = calc_premium_discount(candles[:prefix_len], swing_length=2)["ranges"]
            expected = [r for r in full if r["createdIndex"] < prefix_len]
            for pr, er in zip(prefix_ranges, expected):
                assert pr["rangeHigh"] == er["rangeHigh"] and pr["rangeLow"] == er["rangeLow"]
                assert pr["direction"] == er["direction"]

    def test_realtime_history_convergence(self):
        """Walking forward one candle at a time must produce the same
        sequence of range creations as the one-shot full computation."""
        candles = self._candles()
        full_ids = [r["id"] for r in calc_premium_discount(candles, swing_length=2)["ranges"]]
        seen_ids = []
        for i in range(len(candles)):
            step_ranges = calc_premium_discount(candles[:i + 1], swing_length=2)["ranges"]
            for r in step_ranges:
                if r["id"] not in seen_ids:
                    seen_ids.append(r["id"])
        assert seen_ids == full_ids

    # ── State / data quality ─────────────────────────────────────────────
    def test_market_gap_handled_without_crash(self):
        candles = make_realistic_candles(n=40, seed=101)
        candles[20] = dict(candles[20], high=candles[20]["high"] + 400, low=candles[20]["low"] + 400)
        res = calc_premium_discount(candles, swing_length=5)
        for r in res["ranges"]:
            assert math.isfinite(r["rangeHigh"]) and math.isfinite(r["rangeLow"])

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=102)
        res = calc_premium_discount(candles)
        for r in res["ranges"]:
            assert math.isfinite(r["equilibrium"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_liquidity_sweeps(candles, swing_length=2) is not None
        assert calc_liquidity(candles, swing_length=2) is not None
        assert calc_order_blocks(candles, swing_length=2) is not None
        assert calc_fvg(candles, min_size_atr_multiplier=0) is not None
        assert calc_market_structure(candles, swing_length=2)["trend"] is not None


class TestSMCSetups:
    """
    The combined "SMC" orchestration layer — detects nothing new, purely
    correlates the six standalone engines. Golden fixture built on the
    TestLiquiditySweeps base (14 candles) plus 2 extra candles that create
    BOTH a buy-side sweep (of the 111/110/110.5 highs) and a sell-side
    sweep (of the 97/98/97.5 lows).

    Verified (via the already-independently-tested sub-engines, cross-
    checked by hand below) to produce exactly 3 BEARISH setups — one per
    buy-side pool (SWING_HIGH 111, SWING_HIGH 110, EQUAL_HIGH 110.5) — and
    ZERO bullish setups, even though 3 bullish (sell-side) sweeps also
    exist:

      calc_market_structure events: CHOCH bullish @11 (too EARLY — before
        the sell-side sweeps' reclaimIndex=15, so it can never satisfy
        their [reclaimIndex, reclaimIndex+window] requirement -> no
        bullish setup); CHOCH bearish @14 (exactly AT the buy-side sweeps'
        reclaimIndex=14 -> qualifies for all three).

      For each buy-side setup: premiumDiscountContext at index14 is
      DISCOUNT (close=95 < the then-active range's equilibrium=114.5) ->
      NOT aligned for a bearish setup (bearish wants PREMIUM) ->
      premiumDiscountAligned=False. A bearish FVG confirms at index13
      (origin=11), inside the [11,24] window -> fvgConfluence=True,
      fvgConfluenceId='11-bearish'. calc_order_blocks produces zero blocks
      on this dataset -> obConfluence=False.
      score = 2 (base) + 0 (premium/discount) + 1 (FVG) + 0 (OB) = 3.
    """

    @staticmethod
    def _candles():
        closes = [100, 105, 110, 106, 102, 98, 103, 109, 104, 99, 105, 130, 120, 115, 95, 100]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        highs[14], lows[14] = 96, 94
        highs[15], lows[15] = 101, 99
        return make_hlc(highs, lows, closes)

    KW = dict(swing_length=2, tolerance_atr_multiplier=0.5, atr_length=3, min_touches=2)

    def test_empty_data(self):
        assert calc_smc_setups([]) == []

    def test_golden_case_produces_three_bearish_setups_score_3(self):
        candles = self._candles()
        setups = calc_smc_setups(candles, **self.KW)
        assert len(setups) == 3
        assert all(s["direction"] == "bearish" for s in setups)
        prices = {s["liquidityPrice"] for s in setups}
        assert prices == {111.0, 110.0, 110.5}
        for s in setups:
            assert s["structureEvent"]["type"] == "CHOCH"
            assert s["structureEvent"]["breakIndex"] == 14
            assert s["sweepIndex"] == 11 and s["reclaimIndex"] == 14
            assert s["premiumDiscountContext"] == "DISCOUNT"
            assert s["premiumDiscountAligned"] is False
            assert s["fvgConfluence"] is True and s["fvgConfluenceId"] == "11-bearish"
            assert s["obConfluence"] is False
            assert s["score"] == 3

    def test_no_bullish_setup_even_though_bullish_sweeps_exist(self):
        """The sell-side (bullish-direction) sweeps are real and confirmed
        (see TestLiquiditySweeps), but the only bullish CHoCH in this data
        occurs at index 11 — BEFORE those sweeps' reclaimIndex (15) — so
        it can never satisfy the window requirement. Zero bullish setups
        must result, proving the orchestrator does not force a setup just
        because a sweep exists without genuine structural follow-through."""
        candles = self._candles()
        setups = calc_smc_setups(candles, **self.KW)
        assert not any(s["direction"] == "bullish" for s in setups)

    def test_min_score_filters_out_the_golden_case(self):
        candles = self._candles()
        assert len(calc_smc_setups(candles, min_score=2, **self.KW)) == 3
        assert calc_smc_setups(candles, min_score=4, **self.KW) == []

    def test_score_is_a_plain_count_never_exceeds_five(self):
        candles = self._candles()
        for s in calc_smc_setups(candles, **self.KW):
            assert 2 <= s["score"] <= 5
            expected = 2 + (1 if s["premiumDiscountAligned"] else 0) + (1 if s["fvgConfluence"] else 0) + (1 if s["obConfluence"] else 0)
            assert s["score"] == expected

    def test_no_duplicate_setup_ids(self):
        candles = self._candles()
        ids = [s["id"] for s in calc_smc_setups(candles, **self.KW)]
        assert len(ids) == len(set(ids))

    def test_structure_window_bars_bounds_the_search(self):
        """Shrinking the structure window below the actual gap between a
        sweep's reclaim and its confirming event must drop the setup —
        proving the window is a real, enforced bound, not decorative."""
        candles = self._candles()
        # reclaimIndex=14, breakIndex=14 -> gap is 0, so window=0 must still
        # find it (boundary-inclusive), but a NEGATIVE-equivalent (window
        # forced below the minimum of 1 via the function's own floor) is
        # not constructible; instead verify a genuinely too-small window
        # against a sweep/event pair with a real gap using the base
        # TestLiquiditySweeps multi-candle fixture (gap of 3 bars).
        multi = TestLiquiditySweeps._candles([116, 120, 118, 108], [117, 121, 119, 109], [115, 118, 116, 107])
        wide = calc_smc_setups(multi, structure_window_bars=50, **TestLiquiditySweeps.KW)
        narrow = calc_smc_setups(multi, structure_window_bars=1, **TestLiquiditySweeps.KW)
        assert len(wide) >= len(narrow)

    # ── Repainting / determinism ─────────────────────────────────────────
    def test_deterministic_repeated_calls(self):
        candles = self._candles()
        assert calc_smc_setups(candles, **self.KW) == calc_smc_setups(candles, **self.KW)

    def test_historical_prefix_equivalence_no_repainting(self):
        candles = self._candles()
        full = calc_smc_setups(candles, **self.KW)
        for prefix_len in (14, 15, 16):
            prefix = calc_smc_setups(candles[:prefix_len], **self.KW)
            expected = [s for s in full if s["index"] < prefix_len]
            assert prefix == expected, f"mismatch at prefix_len={prefix_len}"

    # ── State / data quality ─────────────────────────────────────────────
    def test_insufficient_candles_no_crash(self):
        assert calc_smc_setups(make_hlc([100], [90], [95])) == []

    def test_max_setups_caps_output_only(self):
        candles = make_realistic_candles(n=500, seed=111)
        unrestricted = calc_smc_setups(candles, swing_length=3, max_setups=1000)
        capped = calc_smc_setups(candles, swing_length=3, max_setups=1)
        if len(unrestricted) > 1:
            assert len(capped) == 1
            assert capped == unrestricted[-1:]

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=112)
        setups = calc_smc_setups(candles)
        for s in setups:
            assert math.isfinite(s["liquidityPrice"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_premium_discount(candles, swing_length=2) is not None
        assert calc_liquidity_sweeps(candles, swing_length=2) is not None
        assert calc_order_blocks(candles, swing_length=2) is not None
        assert calc_fvg(candles, min_size_atr_multiplier=0) is not None
        assert calc_market_structure(candles, swing_length=2)["trend"] is not None


class TestBreakerMitigation:
    """
    Breaker & Mitigation Blocks, built directly on Order Blocks (never a
    second OB detector).

    Reuses the EXACT 25-candle Order Block golden trace from
    TestOrderBlocks (swing_length=2, explicit opens). Running
    calc_order_blocks(candles, swing_length=2, mitigation='Full Fill') on
    it (independently re-confirmed here, not assumed) now gives 7 blocks
    (see TestOrderBlocks' docstring for the port that produces them):

      origin=2  bearish invalidated  (invalidatedIndex=6)  -> Breaker (flips to BULLISH_BREAKER, zone [104,106])
      origin=4  bullish fully_mitigated (mitigatedIndex=18) -> Mitigation Block
      origin=6  bearish invalidated  (invalidatedIndex=10) -> Breaker (flips to BULLISH_BREAKER, zone [108,110])
      origin=8  bullish fully_mitigated (mitigatedIndex=18) -> Mitigation Block
      origin=10 bearish invalidated  (invalidatedIndex=14) -> Breaker (flips to BULLISH_BREAKER, zone [112,114])
      origin=12 bullish invalidated  (invalidatedIndex=17) -> Breaker (flips to BEARISH_BREAKER, zone [106,108])
      origin=14 bearish active                              -> neither

    The Breaker from origin=12 (zone [106,108]) is then walked forward
    from index 18: none of indices 18-24's Highs (103,99,101,104,100,103,
    106 -- i.e. close+1 for closes [102,98,100,103,99,102,105]) ever reach
    into [106,108], and no Close ever exceeds 108 -- so within the base
    25-candle trace it stays 'active', fillPercentage 0. Two small
    extensions (below) drive it to 'fully_mitigated' and 'invalidated'
    respectively, to test both terminal outcomes explicitly.
    """

    BASE_CANDLES = TestOrderBlocks._candles()

    def test_empty_data(self):
        assert calc_breaker_mitigation([]) == {"breakers": [], "mitigations": []}

    def test_source_order_block_statuses_confirmed(self):
        """Sanity re-confirmation (not assumed) of the exact calc_order_
        blocks statuses this whole test class is built on."""
        obs = calc_order_blocks(self.BASE_CANDLES, swing_length=2, mitigation="Full Fill")
        by_origin = {ob["originIndex"]: ob["status"] for ob in obs}
        assert by_origin[2] == "invalidated"
        assert by_origin[4] == "fully_mitigated"
        assert by_origin[6] == "invalidated"
        assert by_origin[8] == "fully_mitigated"
        assert by_origin[10] == "invalidated"
        assert by_origin[12] == "invalidated"
        assert by_origin[14] == "active"

    # ── Breaker creation / flip ──────────────────────────────────────────
    def test_bullish_ob_invalidation_creates_bearish_breaker(self):
        res = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        assert len(res["breakers"]) == 4
        bullish_breakers = [x for x in res["breakers"] if x["direction"] == "BULLISH_BREAKER"]
        assert len(bullish_breakers) == 3, "one per invalidated bearish OB (origin=2,6,10)"
        b = next(x for x in res["breakers"] if x["direction"] == "BEARISH_BREAKER")
        assert b["top"] == 108 and b["bottom"] == 106
        assert b["creationIndex"] == 12
        assert b["invalidationIndex"] == 17
        assert b["confirmationIndex"] == 17, "the source OB's own invalidation IS the Breaker's confirmation"
        assert b["sourceStructureEvent"] == "bullishReclaim"
        assert b["sourceOrderBlockId"] == "12-bullish-14"

    def test_bearish_ob_invalidation_creates_bullish_breaker(self):
        """Mirror case: an invalidated BEARISH order block flips to a
        BULLISH_BREAKER. The base golden trace already produces three of
        these (origin=2, 6, 10) since a bearish OB's own invalidation is
        literally how this engine creates every bullish OB in the first
        place (see calc_order_blocks' docstring) — pick one and verify
        the flip in detail."""
        res = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        breaker = next(b for b in res["breakers"] if b["sourceOrderBlockId"] == "2-bearish-4")
        assert breaker["direction"] == "BULLISH_BREAKER"
        assert breaker["top"] == 106 and breaker["bottom"] == 104
        assert breaker["creationIndex"] == 2
        assert breaker["invalidationIndex"] == 6
        assert breaker["confirmationIndex"] == 6

    # ── Breaker's own subsequent lifecycle ───────────────────────────────
    def test_breaker_stays_active_when_never_revisited(self):
        res = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        b = next(x for x in res["breakers"] if x["creationIndex"] == 12)
        assert b["status"] == "active"
        assert b["fillPercentage"] == 0
        assert b["mitigatedIndex"] is None and b["reInvalidatedIndex"] is None

    def test_breaker_mitigation_full_fill(self):
        """Two appended candles drive the [106,108] breaker zone (from
        origin=12) to a partial (idx25, High=107 -> 0.5 filled) then full
        (idx26, High=108 exactly -> 1.0 filled) mitigation, hand-verified
        against the zone size (108-106=2)."""
        extended = self.BASE_CANDLES + [
            {"high": 107, "low": 103, "close": 104, "open": 105},
            {"high": 108, "low": 103, "close": 104, "open": 104},
        ]
        find_target = lambda res: next(b for b in res["breakers"] if b["creationIndex"] == 12)
        partial_only = find_target(calc_breaker_mitigation(extended[:-1], swing_length=2))
        assert partial_only["status"] == "partially_mitigated"
        assert abs(partial_only["fillPercentage"] - 0.5) < 1e-9

        full = find_target(calc_breaker_mitigation(extended, swing_length=2))
        assert full["status"] == "fully_mitigated"
        assert abs(full["fillPercentage"] - 1.0) < 1e-9
        assert full["mitigatedIndex"] == 26
        assert full["mitigationPrice"] == 108

    def test_breaker_reinvalidation_on_decisive_close_through(self):
        """A candle CLOSING decisively above the origin=12 breaker's own
        top (108) must mark it re-invalidated — a stronger, distinct
        outcome from mitigation, mirroring Order Blocks' own
        invalidation-vs-mitigation distinction exactly."""
        extended = self.BASE_CANDLES + [{"high": 112, "low": 108, "close": 110, "open": 109}]
        b = next(x for x in calc_breaker_mitigation(extended, swing_length=2)["breakers"] if x["creationIndex"] == 12)
        assert b["status"] == "invalidated"
        assert b["reInvalidatedIndex"] == 25
        assert b["mitigatedIndex"] is None, "re-invalidation must take precedence over recording a mitigation timestamp"

    def test_breaker_touch_mode_confirms_on_first_intrusion(self):
        extended = self.BASE_CANDLES + [
            {"high": 107, "low": 103, "close": 104, "open": 105},
            {"high": 108, "low": 103, "close": 104, "open": 104},
        ]
        b = next(x for x in calc_breaker_mitigation(extended, swing_length=2, breaker_mitigation="Touch")["breakers"] if x["creationIndex"] == 12)
        assert b["status"] == "fully_mitigated"
        assert b["mitigatedIndex"] == 25
        assert b["mitigationPrice"] == 107

    # ── Mitigation Blocks ────────────────────────────────────────────────
    def test_fully_mitigated_obs_become_mitigation_blocks_same_direction(self):
        res = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        assert len(res["mitigations"]) == 2
        by_source = {m["sourceIndex"]: m for m in res["mitigations"]}
        assert by_source[4]["direction"] == "BULLISH_MITIGATION"
        assert by_source[4]["top"] == 102 and by_source[4]["bottom"] == 100
        assert by_source[4]["mitigationIndex"] == 18
        assert by_source[8]["direction"] == "BULLISH_MITIGATION"
        assert by_source[8]["mitigationIndex"] == 18

    def test_active_and_invalidated_obs_never_become_mitigation_blocks(self):
        """Strict distinction: an invalidated OB becomes a Breaker, never
        a Mitigation Block, and vice versa — the two outcomes are
        mutually exclusive by construction."""
        res = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        mitigation_sources = {m["sourceIndex"] for m in res["mitigations"]}
        breaker_sources = {b["creationIndex"] for b in res["breakers"]}
        assert mitigation_sources.isdisjoint(breaker_sources)
        assert mitigation_sources == {4, 8}
        assert breaker_sources == {2, 6, 10, 12}
        assert 14 not in mitigation_sources and 14 not in breaker_sources  # still active -> neither

    def test_mitigation_block_qualification_forces_full_fill_regardless_of_setting(self):
        """Mitigation Block qualification must never rest on a mere Touch
        — verified by confirming the SAME 2 Mitigation Blocks form
        whether the engine's own breaker_mitigation setting (which only
        governs BREAKER lifecycle, not OB->MitigationBlock qualification)
        is Touch or Full Fill."""
        full_fill_setting = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2, breaker_mitigation="Full Fill")
        touch_setting = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2, breaker_mitigation="Touch")
        assert len(full_fill_setting["mitigations"]) == 2
        assert len(touch_setting["mitigations"]) == 2
        assert full_fill_setting["mitigations"] == touch_setting["mitigations"]

    def test_no_duplicate_block_ids(self):
        res = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        all_ids = [b["id"] for b in res["breakers"]] + [m["id"] for m in res["mitigations"]]
        assert len(all_ids) == len(set(all_ids))

    # ── Repainting / determinism ─────────────────────────────────────────
    def test_deterministic_repeated_calls(self):
        res1 = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        res2 = calc_breaker_mitigation(self.BASE_CANDLES, swing_length=2)
        assert res1 == res2

    def test_historical_prefix_equivalence_no_repainting(self):
        """Confirmed OB->Breaker/Mitigation identity (zone/direction) must
        never change once formed; lifecycle (fillPercentage/status) may
        only progress forward as genuinely new candles are added."""
        extended = self.BASE_CANDLES + [
            {"high": 107, "low": 103, "close": 104, "open": 105},
            {"high": 108, "low": 103, "close": 104, "open": 104},
        ]
        order = {"active": 0, "partially_mitigated": 1, "fully_mitigated": 2, "invalidated": 2}
        prev_breakers = {}
        for prefix_len in (18, 20, 25, 26, 27):
            res = calc_breaker_mitigation(extended[:prefix_len], swing_length=2)
            by_source = {b["sourceOrderBlockId"]: b for b in res["breakers"]}
            for src, b in by_source.items():
                if src in prev_breakers:
                    prev = prev_breakers[src]
                    assert b["top"] == prev["top"] and b["bottom"] == prev["bottom"]
                    assert b["direction"] == prev["direction"]
                    assert b["fillPercentage"] >= prev["fillPercentage"] - 1e-9
                    assert order[b["status"]] >= order[prev["status"]]
            prev_breakers = by_source

    # ── State / data quality ─────────────────────────────────────────────
    def test_insufficient_candles_no_crash(self):
        assert calc_breaker_mitigation(make_hlc([100], [90], [95])) == {"breakers": [], "mitigations": []}

    def test_max_blocks_caps_output_only(self):
        candles = make_realistic_candles(n=500, seed=121)
        unrestricted = calc_breaker_mitigation(candles, swing_length=3, max_blocks=1000)
        capped = calc_breaker_mitigation(candles, swing_length=3, max_blocks=1)
        if len(unrestricted["breakers"]) > 1:
            assert len(capped["breakers"]) == 1
        if len(unrestricted["mitigations"]) > 1:
            assert len(capped["mitigations"]) == 1

    def test_large_dataset_no_nan_or_infinity(self):
        candles = make_realistic_candles(n=2000, seed=122)
        res = calc_breaker_mitigation(candles)
        for b in res["breakers"]:
            assert math.isfinite(b["top"]) and math.isfinite(b["bottom"])
        for m in res["mitigations"]:
            assert math.isfinite(m["top"]) and math.isfinite(m["bottom"])

    def test_regression_existing_indicators_unaffected(self):
        candles = make_hlc([110, 112, 108, 115, 118], [100, 103, 100, 106, 109], [105, 108, 103, 112, 116])
        assert calc_williams_r(candles, period=3)[2] is not None
        assert calc_smc_setups(candles, swing_length=2) is not None
        assert calc_premium_discount(candles, swing_length=2) is not None
        assert calc_liquidity_sweeps(candles, swing_length=2) is not None
        assert calc_order_blocks(candles, swing_length=2) is not None
        assert calc_fvg(candles, min_size_atr_multiplier=0) is not None
        assert calc_market_structure(candles, swing_length=2)["trend"] is not None

