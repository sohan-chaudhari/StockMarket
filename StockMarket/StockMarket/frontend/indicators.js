/**
 * LEVERAGE Indicator Engine v1.0
 *
 * Frontend-only indicator calculations from existing chart candle data.
 * Overlay indicators (SMA, EMA, BB) render on the main bigChart.
 * Pane indicators (RSI, MACD) render in separate LWC chart instances below.
 *
 * Public API: window.IndicatorEngine
 *   .addIndicator(id, params?)  → instanceId
 *   .removeIndicator(instanceId)
 *   .toggleIndicator(instanceId)
 *   .updateParams(instanceId, params)
 *   .onCandlesLoaded(candles, range)   ← called by dashboard.js after chart load
 *   .onCandleUpdate(fc)               ← called by dashboard.js on live tick
 */

(function () {
  'use strict';

  // ═══════════════════════════════════════════════════
  // MATH LIBRARY
  // ═══════════════════════════════════════════════════

  var Calc = {
    /**
     * Simple Moving Average — SMA(n) = sum(last n) / n
     */
    sma: function (src, period) {
      var n = src.length;
      var result = new Array(n).fill(null);
      if (period < 1 || n < period) return result;
      var sum = 0;
      for (var i = 0; i < period; i++) sum += src[i];
      result[period - 1] = sum / period;
      for (var i = period; i < n; i++) {
        sum += src[i] - src[i - period];
        result[i] = sum / period;
      }
      return result;
    },

    /**
     * Exponential Moving Average — seeded with first SMA, k = 2/(period+1)
     */
    ema: function (src, period) {
      var n = src.length;
      var result = new Array(n).fill(null);
      if (period < 1 || n < period) return result;
      var sum = 0;
      for (var i = 0; i < period; i++) sum += src[i];
      var prev = sum / period;
      result[period - 1] = prev;
      var k = 2 / (period + 1);
      for (var i = period; i < n; i++) {
        prev = src[i] * k + prev * (1 - k);
        result[i] = prev;
      }
      return result;
    },

    /**
     * RSI using Wilder's smoothing (matches TradingView default)
     * avgGain/Loss seeded with simple mean, then smoothed with 1/period factor
     */
    rsi: function (src, period) {
      var n = src.length;
      var result = new Array(n).fill(null);
      if (period < 1 || n < period + 1) return result;
      var gains = 0, losses = 0;
      for (var i = 1; i <= period; i++) {
        var d = src[i] - src[i - 1];
        if (d > 0) gains += d; else losses -= d;
      }
      var avgGain = gains / period;
      var avgLoss = losses / period;
      if (avgLoss === 0) result[period] = 100;
      else result[period] = 100 - 100 / (1 + avgGain / avgLoss);
      for (var i = period + 1; i < n; i++) {
        var d = src[i] - src[i - 1];
        var g = d > 0 ? d : 0;
        var l = d < 0 ? -d : 0;
        avgGain = (avgGain * (period - 1) + g) / period;
        avgLoss = (avgLoss * (period - 1) + l) / period;
        if (avgLoss === 0) result[i] = 100;
        else result[i] = 100 - 100 / (1 + avgGain / avgLoss);
      }
      return result;
    },

    /**
     * MACD
     *  macdLine  = EMA(fast) - EMA(slow)
     *  signalLine = EMA(macdLine, signal)
     *  histogram  = macdLine - signalLine
     */
    macd: function (src, fast, slow, signal) {
      var n = src.length;
      var empty = function () { return new Array(n).fill(null); };
      var emaFast = this.ema(src, fast);
      var emaSlow = this.ema(src, slow);
      var macdLine = empty();
      for (var i = 0; i < n; i++) {
        if (emaFast[i] !== null && emaSlow[i] !== null)
          macdLine[i] = emaFast[i] - emaSlow[i];
      }
      // Find first non-null MACD index to seed signal EMA
      var firstIdx = -1;
      for (var i = 0; i < n; i++) { if (macdLine[i] !== null) { firstIdx = i; break; } }
      var signalLine = empty(), histogram = empty();
      if (firstIdx === -1) return { macd: macdLine, signal: signalLine, histogram: histogram };
      var macdSlice = macdLine.slice(firstIdx);
      var sigSlice = this.ema(macdSlice, signal);
      for (var i = 0; i < sigSlice.length; i++) {
        var idx = firstIdx + i;
        signalLine[idx] = sigSlice[i];
        if (macdLine[idx] !== null && sigSlice[i] !== null)
          histogram[idx] = macdLine[idx] - sigSlice[i];
      }
      return { macd: macdLine, signal: signalLine, histogram: histogram };
    },

    /**
     * SMA-smooths a value series that starts with a null warm-up prefix
     * (e.g. CCI, or any other already-computed indicator line) without
     * treating those leading nulls as zeros. Finds the first non-null
     * value, runs a plain SMA over the non-null tail only, and re-maps
     * the result back onto the original index positions. Same pattern
     * Calc.macd already uses to seed its signal-line EMA off the MACD
     * line's own warm-up.
     */
    smoothIgnoringNulls: function (vals, period) {
      var n = vals ? vals.length : 0;
      var out = new Array(n).fill(null);
      var firstIdx = -1;
      for (var i = 0; i < n; i++) { if (vals[i] !== null && vals[i] !== undefined && isFinite(vals[i])) { firstIdx = i; break; } }
      if (firstIdx === -1) return out;
      var tail = vals.slice(firstIdx);
      var smoothed = this.sma(tail, period);
      for (var i = 0; i < smoothed.length; i++) {
        out[firstIdx + i] = smoothed[i];
      }
      return out;
    },

    /**
     * Bollinger Bands — basis=SMA, stddev is population (÷n not ÷n-1)
     */
    bollingerBands: function (src, period, mult) {
      var n = src.length;
      var basis = this.sma(src, period);
      var upper = new Array(n).fill(null);
      var lower = new Array(n).fill(null);
      for (var i = period - 1; i < n; i++) {
        if (basis[i] === null) continue;
        var variance = 0;
        for (var j = i - period + 1; j <= i; j++) {
          var d = src[j] - basis[i];
          variance += d * d;
        }
        var sd = Math.sqrt(variance / period);
        upper[i] = basis[i] + mult * sd;
        lower[i] = basis[i] - mult * sd;
      }
      return { basis: basis, upper: upper, lower: lower };
    },

    /**
     * Volume Weighted Average Price (VWAP)
     * Session-anchored: resets cumulative volume and price-volume on new trading days (IST session boundary).
     * Returns: { vwap: Array, upper: Array, lower: Array, stdDev: Array }
     */
    vwap: function (candles, sourceField, anchor, bandMult, bandsEnabled) {
      var n = candles ? candles.length : 0;
      var vwap = new Array(n).fill(null);
      var upper = new Array(n).fill(null);
      var lower = new Array(n).fill(null);
      var stdDev = new Array(n).fill(null);
      if (!candles || n === 0) return { vwap: vwap, upper: upper, lower: lower, stdDev: stdDev };

      var src = Calc.source(candles, sourceField || 'hlc3');
      var mult = Number(bandMult) || 1.0;
      var showBands = (bandsEnabled === 'On' || bandsEnabled === true);

      var cumVol = 0;
      var cumPriceVol = 0;
      var cumPrice2Vol = 0;
      var currentSessionKey = null;

      for (var i = 0; i < n; i++) {
        var c = candles[i];
        var sKey = Calc.candleSessionKey(c, i);
        if (anchor === 'Session' || !anchor) {
          if (currentSessionKey !== null && sKey !== currentSessionKey) {
            // New trading session boundary: reset cumulative totals
            cumVol = 0;
            cumPriceVol = 0;
            cumPrice2Vol = 0;
          }
          currentSessionKey = sKey;
        }

        var tp = src[i];
        var v = (c.volume !== undefined && c.volume !== null) ? Number(c.volume) : 0;
        if (!isFinite(v) || v < 0) v = 0;

        if (v > 0 && isFinite(tp)) {
          cumVol += v;
          cumPriceVol += tp * v;
          cumPrice2Vol += tp * tp * v;
        }

        if (cumVol > 0) {
          var val = cumPriceVol / cumVol;
          vwap[i] = val;

          if (showBands) {
            var variance = (cumPrice2Vol / cumVol) - (val * val);
            var sd = Math.sqrt(Math.max(0, variance));
            stdDev[i] = sd;
            upper[i] = val + mult * sd;
            lower[i] = val - mult * sd;
          }
        } else {
          // If no volume has accumulated in session yet, fallback safely to TP
          vwap[i] = isFinite(tp) ? tp : null;
          if (showBands && isFinite(tp)) {
            upper[i] = tp;
            lower[i] = tp;
            stdDev[i] = 0;
          }
        }
      }

      return { vwap: vwap, upper: upper, lower: lower, stdDev: stdDev };
    },

    /** Extract IST session date key (YYYY-MM-DD) from candle timestamp */
    candleSessionKey: function (c, index) {
      if (!c) return '';
      var realT = null;
      if (typeof index === 'number' && window._intradayBarTimes && window._intradayBarTimes[index] !== undefined) {
        realT = window._intradayBarTimes[index];
      } else if (c._realTime !== undefined && c._realTime !== null) {
        realT = c._realTime;
      } else {
        realT = c.time;
      }

      if (realT === undefined || realT === null) return '';
      if (typeof realT === 'object' && realT.year && realT.month && realT.day) {
        return realT.year + '-' + (realT.month < 10 ? '0' : '') + realT.month + '-' + (realT.day < 10 ? '0' : '') + realT.day;
      }
      if (typeof realT === 'string') {
        return realT.slice(0, 10);
      }
      if (typeof realT === 'number') {
        var sec = realT > 1e11 ? Math.floor(realT / 1000) : realT;
        var d = new Date(sec * 1000 + 19800000); // 5.5 * 3600 * 1000 = +05:30 IST
        var y = d.getUTCFullYear();
        var m = d.getUTCMonth() + 1;
        var day = d.getUTCDate();
        return y + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
      }
      return String(realT);
    },

    /** ISO-8601 week key ('yyyy-Www') from a 'yyyy-mm-dd' session day key. */
    _isoWeekKey: function (dayKey) {
      if (!dayKey) return '';
      var parts = dayKey.split('-');
      var d = new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2]));
      var dayNum = (d.getUTCDay() + 6) % 7; // Mon=0..Sun=6
      d.setUTCDate(d.getUTCDate() - dayNum + 3); // Thursday of this ISO week
      var firstThursday = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
      var weekNum = 1 + Math.round(((d - firstThursday) / 86400000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7);
      return d.getUTCFullYear() + '-W' + (weekNum < 10 ? '0' : '') + weekNum;
    },

    /** Formula table for the 4 supported pivot methods. R4/S4 are only
     *  defined for Camarilla; other methods leave them null (no invented
     *  levels). */
    _pivotLevelsForMethod: function (method, H, L, C) {
      var range = H - L;
      if (method === 'Fibonacci') {
        var p1 = (H + L + C) / 3;
        return {
          p: p1,
          r1: p1 + 0.382 * range, r2: p1 + 0.618 * range, r3: p1 + 1.000 * range, r4: null, r5: null,
          s1: p1 - 0.382 * range, s2: p1 - 0.618 * range, s3: p1 - 1.000 * range, s4: null, s5: null
        };
      }
      if (method === 'Woodie') {
        var p2 = (H + L + 2 * C) / 4;
        return {
          p: p2,
          r1: 2 * p2 - L, r2: p2 + range, r3: H + 2 * (p2 - L), r4: null, r5: null,
          s1: 2 * p2 - H, s2: p2 - range, s3: L - 2 * (H - p2), s4: null, s5: null
        };
      }
      if (method === 'Camarilla') {
        return {
          p: (H + L + C) / 3,
          r1: C + range * 1.1 / 12, r2: C + range * 1.1 / 6, r3: C + range * 1.1 / 4, r4: C + range * 1.1 / 2, r5: null,
          s1: C - range * 1.1 / 12, s2: C - range * 1.1 / 6, s3: C - range * 1.1 / 4, s4: C - range * 1.1 / 2, s5: null
        };
      }
      // Standard ("Traditional" in TradingView's naming). R4/R5/S4/S5 use
      // TradingView's actual documented Traditional formula — NOT an
      // "arithmetic progression continuation" of R1-R3 (a prior version of
      // this function guessed that shape; it does not match TradingView:
      // for H=115,L=100,C=108, the guessed formula gives R4=137.667 while
      // TradingView's real R4 (3P+H-3L) gives 138 — a real bug, now fixed).
      var p3 = (H + L + C) / 3;
      return {
        p: p3,
        r1: 2 * p3 - L, r2: p3 + range, r3: H + 2 * (p3 - L), r4: 3 * p3 + H - 3 * L, r5: 4 * p3 + H - 4 * L,
        s1: 2 * p3 - H, s2: p3 - range, s3: L - 2 * (H - p3), s4: 3 * p3 - 3 * H + L, s5: 4 * p3 - 4 * H + L
      };
    },

    /**
     * Pivot Points (Standard / Fibonacci / Woodie / Camarilla), calculated
     * per completed trading period (Daily/Weekly/Monthly) and applied to
     * the FOLLOWING period only — this is the entire no-look-ahead
     * guarantee: period k's levels are computed from period k-1's fully
     * completed High/Low/Close, and every candle in period k (including
     * its own still-forming last candle) gets those same fixed values.
     * Period k's own high/low/close is never used for period k's levels.
     *
     * Trading-session boundaries reuse Calc.candleSessionKey — the same
     * IST-aware session-day utility VWAP already uses — rather than a
     * separate calendar implementation. Since the underlying candle
     * dataset only contains actual trading sessions (no synthetic
     * holiday/weekend rows), grouping by that key's day-to-day CHANGES
     * automatically skips weekends/holidays: "the previous period" is
     * always the previous row of real data, never a naive UTC/calendar
     * adjacency assumption.
     *
     * The first period in the dataset has no prior period, so its candles
     * get null levels (same "insufficient warm-up" convention used
     * everywhere else in this engine) — never a fabricated period 0.
     */
    pivotPoints: function (candles, method, period) {
      var n = candles ? candles.length : 0;
      var levelKeys = ['p', 'r1', 'r2', 'r3', 'r4', 'r5', 's1', 's2', 's3', 's4', 's5'];
      var out = {};
      levelKeys.forEach(function (k) { out[k] = new Array(n).fill(null); });
      if (!candles || n === 0) return out;

      var per = period || 'Daily';
      if (per === 'Auto') {
        // Matches TradingView's documented Auto rule for the intraday
        // range: Daily pivots up to and including 15-minute charts,
        // Weekly pivots above 15 minutes and below 1 day. TradingView
        // steps the period up further for 1D+ charts (Monthly/Yearly);
        // the exact thresholds there aren't independently verified here,
        // so 1-day-or-longer bars conservatively fall back to Monthly
        // rather than guessing a Yearly cutoff.
        var barSeconds = Calc._ichimokuBarInterval(candles);
        if (barSeconds <= 15 * 60) per = 'Daily';
        else if (barSeconds < 86400) per = 'Weekly';
        else per = 'Monthly';
      }

      var periodKeys = new Array(n);
      for (var i = 0; i < n; i++) {
        var dayKey = Calc.candleSessionKey(candles[i], i);
        if (per === 'Weekly') periodKeys[i] = Calc._isoWeekKey(dayKey);
        else if (per === 'Monthly') periodKeys[i] = dayKey ? dayKey.slice(0, 7) : '';
        else periodKeys[i] = dayKey; // Daily
      }

      // Group into ordered periods, tracking each one's own H/L/C.
      var periods = [];
      var curKey = null, curPeriod = null;
      for (var i = 0; i < n; i++) {
        if (periodKeys[i] !== curKey) {
          curKey = periodKeys[i];
          curPeriod = { startIdx: i, endIdx: i, high: -Infinity, low: Infinity, close: null };
          periods.push(curPeriod);
        }
        var h = Number(candles[i].high);
        var l = Number(candles[i].low);
        var c = Number(candles[i].close);
        if (!isFinite(c)) c = null;
        if (!isFinite(h)) h = (c !== null) ? c : curPeriod.high;
        if (!isFinite(l)) l = (c !== null) ? c : curPeriod.low;
        if (h > curPeriod.high) curPeriod.high = h;
        if (l < curPeriod.low) curPeriod.low = l;
        if (c !== null) curPeriod.close = c;
        curPeriod.endIdx = i;
      }

      // Apply period k-1's completed OHLC to period k's candles.
      for (var k = 1; k < periods.length; k++) {
        var prev = periods[k - 1];
        if (!isFinite(prev.high) || !isFinite(prev.low) || prev.close === null) continue;
        var levels = Calc._pivotLevelsForMethod(method, prev.high, prev.low, prev.close);
        var cur = periods[k];
        for (var idx = cur.startIdx; idx <= cur.endIdx; idx++) {
          for (var li = 0; li < levelKeys.length; li++) {
            var lk = levelKeys[li];
            out[lk][idx] = (levels[lk] !== undefined) ? levels[lk] : null;
          }
        }
      }

      return out;
    },

    /** Extract source values from candle array */
    source: function (candles, field) {
      if (!candles || !candles.length) return [];
      return candles.map(function (c) {
        var cl = Number(c.close);
        if (!isFinite(cl)) cl = 0;
        var o = (c.open !== undefined && c.open !== null) ? Number(c.open) : cl;
        if (!isFinite(o)) o = cl;
        var h = (c.high !== undefined && c.high !== null) ? Number(c.high) : Math.max(o, cl);
        if (!isFinite(h)) h = Math.max(o, cl);
        var l = (c.low !== undefined && c.low !== null) ? Number(c.low) : Math.min(o, cl);
        if (!isFinite(l)) l = Math.min(o, cl);

        switch (field) {
          case 'open':  return o;
          case 'high':  return h;
          case 'low':   return l;
          case 'hl2':   return (h + l) / 2;
          case 'hlc3':  return (h + l + cl) / 3;
          case 'ohlc4': return (o + h + l + cl) / 4;
          case 'close':
          default:      return cl;
        }
      });
    },

    /**
     * Average True Range (ATR) — Wilder's Smoothing Methodology
     * TR = max(H - L, |H - prevClose|, |L - prevClose|) (TR0 = H0 - L0)
     * Initial ATR at index (period - 1) = SMA(TR, period)
     * Subsequent ATR = (ATR_prev * (period - 1) + TR) / period
     * Returns: Array of length n (first period - 1 entries are null)
     */
    atr: function (candles, period) {
      var n = candles ? candles.length : 0;
      var result = new Array(n).fill(null);
      var p = parseInt(period, 10);
      if (!candles || n === 0 || isNaN(p) || p < 1) return result;

      // 1. Calculate True Range array
      var tr = new Array(n);
      for (var i = 0; i < n; i++) {
        var c = candles[i];
        var h = Number(c.high);
        var l = Number(c.low);
        var cl = Number(c.close);
        if (!isFinite(h)) h = cl;
        if (!isFinite(l)) l = cl;
        if (!isFinite(cl)) cl = (h + l) / 2;

        if (i === 0) {
          tr[i] = Math.abs(h - l);
        } else {
          var prev = candles[i - 1];
          var prevClose = Number(prev.close);
          if (!isFinite(prevClose)) prevClose = cl;
          var hl = Math.abs(h - l);
          var hpc = Math.abs(h - prevClose);
          var lpc = Math.abs(l - prevClose);
          tr[i] = Math.max(hl, hpc, lpc);
        }
      }

      // 2. Special case period = 1: ATR(1) = TR
      if (p === 1) {
        for (var i = 0; i < n; i++) {
          result[i] = tr[i];
        }
        return result;
      }

      // 3. Minimum warm-up check
      if (n < p) return result;

      // 4. Initial ATR seed: simple average of first p True Ranges
      var sum = 0;
      for (var i = 0; i < p; i++) {
        sum += tr[i];
      }
      var prevAtr = sum / p;
      result[p - 1] = prevAtr;

      // 5. Wilder's recurrence smoothing for subsequent bars
      for (var i = p; i < n; i++) {
        var curAtr = (prevAtr * (p - 1) + tr[i]) / p;
        result[i] = curAtr;
        prevAtr = curAtr;
      }

      return result;
    },

    /**
     * Supertrend Trend-Following Overlay Indicator
     * Reuses Calc.atr(candles, period)
     * BasicUpper = (H + L) / 2 + multiplier * ATR
     * BasicLower = (H + L) / 2 - multiplier * ATR
     * FinalUpper[i] = (BasicUpper[i] < FinalUpper[i-1] || Close[i-1] > FinalUpper[i-1]) ? BasicUpper[i] : FinalUpper[i-1]
     * FinalLower[i] = (BasicLower[i] > FinalLower[i-1] || Close[i-1] < FinalLower[i-1]) ? BasicLower[i] : FinalLower[i-1]
     * Returns: { supertrend: Array, trend: Array (1 for UP, -1 for DOWN), upper: Array, lower: Array }
     */
    supertrend: function (candles, period, multiplier) {
      var n = candles ? candles.length : 0;
      var nullArr = function () { return new Array(n).fill(null); };
      var p = parseInt(period, 10);
      var m = parseFloat(multiplier);
      if (!candles || n === 0 || isNaN(p) || p < 1 || isNaN(m) || m <= 0) {
        return { supertrend: nullArr(), trend: nullArr(), upper: nullArr(), lower: nullArr() };
      }

      // 1. Reuse existing authoritative ATR engine
      var atr = Calc.atr(candles, p);

      var supertrend = nullArr();
      var trend = nullArr();
      var finalUpper = nullArr();
      var finalLower = nullArr();

      // Warm-up check
      if (n < p) {
        return { supertrend: supertrend, trend: trend, upper: finalUpper, lower: finalLower };
      }

      var seedIdx = p - 1;
      for (var i = seedIdx; i < n; i++) {
        var c = candles[i];
        var h = Number(c.high);
        var l = Number(c.low);
        var cl = Number(c.close);
        if (!isFinite(h)) h = cl;
        if (!isFinite(l)) l = cl;
        if (!isFinite(cl)) cl = (h + l) / 2;

        var curAtr = atr[i];
        if (curAtr === null || !isFinite(curAtr)) continue;

        var hl2 = (h + l) / 2.0;
        var basicUpper = hl2 + (m * curAtr);
        var basicLower = hl2 - (m * curAtr);

        if (i === seedIdx || finalUpper[i - 1] === null) {
          finalUpper[i] = basicUpper;
          finalLower[i] = basicLower;
          var initTrend = (cl > basicUpper) ? 1 : (cl < basicLower) ? -1 : (cl >= hl2) ? 1 : -1;
          trend[i] = initTrend;
          supertrend[i] = initTrend === 1 ? basicLower : basicUpper;
        } else {
          var prevFinalUpper = finalUpper[i - 1];
          var prevFinalLower = finalLower[i - 1];
          var prevClose = Number(candles[i - 1].close);
          if (!isFinite(prevClose)) prevClose = cl;
          var prevTrend = trend[i - 1];

          // Final Upper Band
          if (basicUpper < prevFinalUpper || prevClose > prevFinalUpper) {
            finalUpper[i] = basicUpper;
          } else {
            finalUpper[i] = prevFinalUpper;
          }

          // Final Lower Band
          if (basicLower > prevFinalLower || prevClose < prevFinalLower) {
            finalLower[i] = basicLower;
          } else {
            finalLower[i] = prevFinalLower;
          }

          // Trend Determination
          if (prevTrend === -1) {
            if (cl > finalUpper[i]) {
              trend[i] = 1; // Flip to UP
              supertrend[i] = finalLower[i];
            } else {
              trend[i] = -1; // Remain DOWN
              supertrend[i] = finalUpper[i];
            }
          } else {
            if (cl < finalLower[i]) {
              trend[i] = -1; // Flip to DOWN
              supertrend[i] = finalUpper[i];
            } else {
              trend[i] = 1; // Remain UP
              supertrend[i] = finalLower[i];
            }
          }
        }
      }

      return {
        supertrend: supertrend,
        trend: trend,
        upper: finalUpper,
        lower: finalLower
      };
    },

    /**
     * Keltner Channels — EMA centerline with ATR-based volatility bands.
     * Middle[i] = EMA(close, length)[i]      (reuses Calc.ema verbatim)
     * Upper[i]  = Middle[i] + ATR(atrLength)[i] * multiplier  (reuses Calc.atr verbatim)
     * Lower[i]  = Middle[i] - ATR(atrLength)[i] * multiplier
     * Deliberately NOT SMA ± stddev (that's Bollinger Bands) and NOT
     * rolling highest-high/lowest-low (that's Donchian Channels) — the two
     * indicators this one is most often confused with.
     */
    keltner: function (candles, length, atrLength, multiplier) {
      var n = candles ? candles.length : 0;
      var middle = new Array(n).fill(null);
      var upper = new Array(n).fill(null);
      var lower = new Array(n).fill(null);

      var len = parseInt(length, 10);
      var atrLen = parseInt(atrLength, 10);
      var mult = parseFloat(multiplier);
      if (!candles || n === 0 || isNaN(len) || len < 1 || isNaN(atrLen) || atrLen < 1 ||
          isNaN(mult) || !isFinite(mult) || mult <= 0) {
        return { middle: middle, upper: upper, lower: lower };
      }

      var emaVals = Calc.ema(Calc.source(candles, 'close'), len);
      var atrVals = Calc.atr(candles, atrLen);

      for (var i = 0; i < n; i++) {
        if (emaVals[i] === null || atrVals[i] === null) continue;
        middle[i] = emaVals[i];
        upper[i] = emaVals[i] + atrVals[i] * mult;
        lower[i] = emaVals[i] - atrVals[i] * mult;
      }

      return { middle: middle, upper: upper, lower: lower };
    },

    /**
     * Parabolic SAR (Wilder).
     *
     * Initialization convention (index 0 and 1 are the only "manufactured"
     * state — every real PSAR implementation must seed a starting
     * direction/SAR/EP somehow, since the formula is a recurrence with no
     * natural t=0 value):
     *   - Index 0: no PSAR (insufficient history) -> null/null.
     *   - Index 1: initial trend is UP if close[1] >= close[0], else DOWN.
     *     Initial SAR = low[0] (uptrend) or high[0] (downtrend) — the prior
     *     candle's opposite extreme. Initial EP = high[1] (uptrend) or
     *     low[1] (downtrend) — the seed candle's own extreme.
     *   - From index 2 onward, the standard Wilder recurrence runs:
     *       candidateSar = prevSar + AF * (EP - prevSar)
     *       uptrend:   candidateSar = min(candidateSar, low[i-1], low[i-2])
     *       downtrend: candidateSar = max(candidateSar, high[i-1], high[i-2])
     *     A reversal occurs when price crosses the constrained candidate
     *     (low[i] <= candidate in an uptrend, high[i] >= candidate in a
     *     downtrend); on reversal, SAR resets to the previous EP, EP resets
     *     to the current candle's opposite extreme, and AF resets to
     *     initialAF. Otherwise EP/AF only advance when a NEW extreme is
     *     set, with AF capped at maximumAF.
     * Every index i is computed using only candles[0..i] — no candle ahead
     * of i is ever read, so there is no look-ahead bias.
     */
    psar: function (candles, initialAF, increment, maximumAF) {
      var n = candles ? candles.length : 0;
      var sar = new Array(n).fill(null);
      var trend = new Array(n).fill(null);

      var iAF = parseFloat(initialAF);
      var inc = parseFloat(increment);
      var maxAF = parseFloat(maximumAF);
      var valid = !isNaN(iAF) && iAF > 0 &&
                  !isNaN(inc) && inc > 0 &&
                  !isNaN(maxAF) && maxAF > 0 &&
                  iAF <= maxAF && inc <= maxAF;
      if (!candles || n < 2 || !valid) return { sar: sar, trend: trend };

      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : Number(candles[i].close); }
      function L(i) { var v = Number(candles[i].low);  return isFinite(v) ? v : Number(candles[i].close); }

      // Seed at index 1 from candles 0 and 1 only.
      var closeUp = Number(candles[1].close) >= Number(candles[0].close);
      var curTrend = closeUp ? 1 : -1;
      var ep = closeUp ? H(1) : L(1);
      sar[1] = closeUp ? L(0) : H(0);
      trend[1] = curTrend;
      var af = iAF;

      for (var i = 2; i < n; i++) {
        var prevSar = sar[i - 1];
        var candidate = prevSar + af * (ep - prevSar);

        if (curTrend === 1) {
          candidate = Math.min(candidate, L(i - 1), L(i - 2));
          if (L(i) <= candidate) {
            // Reversal to downtrend
            curTrend = -1;
            sar[i] = ep;
            ep = L(i);
            af = iAF;
          } else {
            sar[i] = candidate;
            if (H(i) > ep) {
              ep = H(i);
              af = Math.min(af + inc, maxAF);
            }
          }
        } else {
          candidate = Math.max(candidate, H(i - 1), H(i - 2));
          if (H(i) >= candidate) {
            // Reversal to uptrend
            curTrend = 1;
            sar[i] = ep;
            ep = H(i);
            af = iAF;
          } else {
            sar[i] = candidate;
            if (L(i) < ep) {
              ep = L(i);
              af = Math.min(af + inc, maxAF);
            }
          }
        }
        trend[i] = curTrend;
      }

      return { sar: sar, trend: trend };
    },

    /**
     * Pivot Points High Low — local swing-point detection with an explicit
     * confirmation delay (a distinct indicator from Pivot Points Standard;
     * this has nothing to do with prior-period support/resistance levels).
     *
     * Equality/tie convention (deterministic, documented, tested): a
     * candidate at index i is a pivot high only if its high is STRICTLY
     * greater than every high in the left window [i-left, i-1], and
     * greater-than-OR-EQUAL to every high in the right window [i+1, i+right].
     * Pivot low mirrors this: strictly LESS than the left window, less-than-
     * -or-equal to the right window. This asymmetry is deliberate: on a
     * flat plateau, only the FIRST candle of the tie can ever pass the
     * strict left-side check, so a plateau produces exactly one pivot, not
     * one per tied candle.
     *
     * Pivot location vs confirmation: a pivot AT index i is only knowable
     * once `rightBars` candles after it exist. This function never invents
     * that data — it simply doesn't evaluate (leaves null) any index i for
     * which i+rightBars is beyond the end of the supplied candles array.
     * The moment the caller supplies more candles (a new live tick, or
     * historical backfill), a previously-null index can become non-null on
     * the next call — that IS the confirmation event. The value, when
     * present, is always placed at the original pivot candle's OWN index
     * (never shifted to the confirmation index).
     */
    pivotHighLow: function (candles, leftBars, rightBars) {
      var n = candles ? candles.length : 0;
      var pivotHigh = new Array(n).fill(null);
      var pivotLow = new Array(n).fill(null);

      var L = parseInt(leftBars, 10);
      var R = parseInt(rightBars, 10);
      if (!candles || n === 0 || isNaN(L) || L < 1 || isNaN(R) || R < 1) {
        return { pivotHigh: pivotHigh, pivotLow: pivotLow };
      }

      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : Number(candles[i].close); }
      function Lo(i) { var v = Number(candles[i].low); return isFinite(v) ? v : Number(candles[i].close); }

      for (var i = L; i < n - R; i++) {
        var h = H(i);
        var isHigh = true;
        for (var j = i - L; j < i; j++) { if (!(h > H(j))) { isHigh = false; break; } }
        if (isHigh) {
          for (var j = i + 1; j <= i + R; j++) { if (!(h >= H(j))) { isHigh = false; break; } }
        }
        if (isHigh) pivotHigh[i] = h;

        var l = Lo(i);
        var isLow = true;
        for (var j = i - L; j < i; j++) { if (!(l < Lo(j))) { isLow = false; break; } }
        if (isLow) {
          for (var j = i + 1; j <= i + R; j++) { if (!(l <= Lo(j))) { isLow = false; break; } }
        }
        if (isLow) pivotLow[i] = l;
      }

      return { pivotHigh: pivotHigh, pivotLow: pivotLow };
    },

    /**
     * Market Structure Engine — swing detection, HH/HL/LH/LL classification,
     * and BOS/CHoCH/MSS break events. Foundation for future Smart Money
     * Concepts (SMC) indicators (FVG, Order Blocks, Liquidity, etc.), which
     * should read `swings`/`events`/`trend` from this single calculation
     * rather than re-deriving swing points themselves.
     *
     * ── Swing detection ──────────────────────────────────────────────────
     * Reuses Calc.pivotHighLow's exact confirmation-delay convention
     * (symmetric left/right window = swingLength, same strict-left/
     * lenient-right tie rule) rather than redefining swing detection — a
     * pivot at index p is only CONFIRMED once swingLength real candles
     * exist after it (confirmedAt = p + swingLength). This module doesn't
     * call Calc.pivotHighLow directly because the break/trend state machine
     * below needs each swing's confirmedAt index explicitly (see next
     * section), but the detection loop is byte-for-byte the same rule.
     *
     * ── HH/HL/LH/LL classification ───────────────────────────────────────
     * Each swing is classified against the PREVIOUS CONFIRMED swing of the
     * SAME type only (a new high vs. the last high, a new low vs. the last
     * low) — independent of the break/trend state machine below. This is
     * informational (drives the optional swing labels) and does not decide
     * BOS/CHoCH/MSS.
     *
     * ── Break / trend state machine (non-repainting) ─────────────────────
     * Walks candles chronologically. A swing only becomes an active
     * structural level once the CANDLE INDEX reaches that swing's own
     * confirmedAt — never earlier — so a full-history recompute produces
     * exactly the same events a live, candle-by-candle feed would have
     * produced. `trend` starts 'neutral' and is only SET by the first
     * break event (whichever direction that break is, since there is no
     * prior trend to compare against); after that:
     *
     *   - A break in the SAME direction as the current trend  -> BOS
     *     (continuation of existing structure).
     *   - A break in the OPPOSITE direction (a reversal)      -> CHoCH,
     *     and `trend` flips to the new direction. If the breaking candle
     *     also shows DISPLACEMENT — |close-open| >= displacementMultiplier
     *     * ATR(atrLength) at the break bar (reuses Calc.atr verbatim,
     *     computed once up front) — the event is classified MSS instead of
     *     CHoCH. This is LEVERAGE's own explicit, documented definition:
     *     "MSS = a reversal break (what would otherwise be a CHoCH) whose
     *     breaking candle demonstrates strong impulsive displacement."
     *     Other platforms define MSS differently (some treat it as a pure
     *     CHoCH synonym, others require multiple consecutive breaks) — this
     *     project picked a deterministic, configurable rule over an
     *     undocumented alias. A reversal break is therefore classified as
     *     EITHER CHoCH or MSS, never both, so BOS/CHoCH/MSS partition every
     *     break event with no duplicate labels for the same event.
     *   - Each broken swing level is marked `broken` immediately, so the
     *     same level can never fire a second event later.
     *
     * One pass produces `swings` and `events` for all three event types —
     * BOS/CHoCH/MSS are display-time filters over `events`, not three
     * independent calculations (see the DEFS/_drawOverlayClouds side).
     *
     * Deliberate deviation from a strictly "incremental" engine: this is a
     * pure, full-recompute-per-call function, not a persistent mutable
     * streaming engine. That matches EVERY other stateful indicator in
     * this file (Supertrend, PSAR, RSI, MACD, Keltner, ATR) and is this
     * codebase's established, explicitly-reasoned tradeoff at the candle
     * counts it actually renders (hundreds, not millions) — see Calc.atr's
     * and Calc.keltner's own callers for the identical pattern. Purity
     * plus the confirmedAt gating above is what actually guarantees
     * non-repainting and realtime/historical equivalence, not statefulness
     * for its own sake; the caller still only pushes the LAST bar's change
     * to the chart via .update(), so rendering remains incremental even
     * though calculation is not.
     */
    marketStructure: function (candles, swingLength, confirmationType, displacementMultiplier, atrLength) {
      var n = candles ? candles.length : 0;
      var L = parseInt(swingLength, 10);
      var dispMult = parseFloat(displacementMultiplier);
      var atrLen = parseInt(atrLength, 10);
      var empty = { swings: [], events: [], trend: 'neutral' };
      if (!candles || n === 0 || isNaN(L) || L < 1) return empty;
      if (isNaN(dispMult) || dispMult <= 0) dispMult = 1.5;
      if (isNaN(atrLen) || atrLen < 1) atrLen = 14;

      var confirmByClose = (confirmationType !== 'Wick');

      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : Number(candles[i].close); }
      function Lo(i) { var v = Number(candles[i].low); return isFinite(v) ? v : Number(candles[i].close); }
      function C(i) { var v = Number(candles[i].close); return isFinite(v) ? v : 0; }
      function O(i) { var v = Number(candles[i].open); return isFinite(v) ? v : C(i); }

      // 1. Confirmed swing highs/lows (same rule as Calc.pivotHighLow).
      var swings = [];
      for (var p = L; p < n - L; p++) {
        var h = H(p), isHigh = true;
        for (var j = p - L; j < p; j++) { if (!(h > H(j))) { isHigh = false; break; } }
        if (isHigh) { for (var j2 = p + 1; j2 <= p + L; j2++) { if (!(h >= H(j2))) { isHigh = false; break; } } }
        if (isHigh) {
          swings.push({ index: p, confirmedAt: p + L, time: candles[p].time, price: h, type: 'high', classification: null, broken: false });
        }
        var l = Lo(p), isLow = true;
        for (var j3 = p - L; j3 < p; j3++) { if (!(l < Lo(j3))) { isLow = false; break; } }
        if (isLow) { for (var j4 = p + 1; j4 <= p + L; j4++) { if (!(l <= Lo(j4))) { isLow = false; break; } } }
        if (isLow) {
          swings.push({ index: p, confirmedAt: p + L, time: candles[p].time, price: l, type: 'low', classification: null, broken: false });
        }
      }
      swings.sort(function (a, b) { return a.index - b.index; });

      // 2. HH/HL/LH/LL classification (informational; independent of trend).
      var prevHigh = null, prevLow = null;
      for (var s = 0; s < swings.length; s++) {
        var sw = swings[s];
        if (sw.type === 'high') {
          if (prevHigh !== null) sw.classification = (sw.price > prevHigh.price) ? 'HH' : 'LH';
          prevHigh = sw;
        } else {
          if (prevLow !== null) sw.classification = (sw.price > prevLow.price) ? 'HL' : 'LL';
          prevLow = sw;
        }
      }

      // ATR computed once up front and reused for every MSS displacement
      // check — never a second ATR definition.
      var atrVals = Calc.atr(candles, atrLen);

      // 3. Break / trend state machine.
      var trend = 'neutral';
      var activeHigh = null;
      var activeLow = null;
      var revealPtr = 0;
      var events = [];

      for (var i = 0; i < n; i++) {
        while (revealPtr < swings.length && swings[revealPtr].confirmedAt <= i) {
          var revealed = swings[revealPtr];
          if (revealed.type === 'high') activeHigh = revealed;
          else activeLow = revealed;
          revealPtr++;
        }

        var upVal = confirmByClose ? C(i) : H(i);
        var downVal = confirmByClose ? C(i) : Lo(i);
        var atrHere = atrVals[i];
        var displaced = (atrHere !== null && isFinite(atrHere) && atrHere > 0) &&
          (Math.abs(C(i) - O(i)) >= dispMult * atrHere);

        if (activeHigh && !activeHigh.broken && upVal > activeHigh.price) {
          activeHigh.broken = true;
          var upType = (trend === 'bullish') ? 'BOS' : (displaced ? 'MSS' : 'CHOCH');
          events.push({
            type: upType, direction: 'bullish', breakIndex: i, breakTime: candles[i].time,
            brokenLevel: activeHigh.price, originSwingIndex: activeHigh.index, originSwingTime: activeHigh.time,
            confirmationType: confirmByClose ? 'Close' : 'Wick'
          });
          if (trend !== 'bullish') trend = 'bullish';
        }
        if (activeLow && !activeLow.broken && downVal < activeLow.price) {
          activeLow.broken = true;
          var downType = (trend === 'bearish') ? 'BOS' : (displaced ? 'MSS' : 'CHOCH');
          events.push({
            type: downType, direction: 'bearish', breakIndex: i, breakTime: candles[i].time,
            brokenLevel: activeLow.price, originSwingIndex: activeLow.index, originSwingTime: activeLow.time,
            confirmationType: confirmByClose ? 'Close' : 'Wick'
          });
          if (trend !== 'bearish') trend = 'bearish';
        }
      }

      return { swings: swings, events: events, trend: trend };
    },

    /**
     * Fair Value Gap (FVG) — standard 3-candle imbalance model. A separate,
     * independent engine from Calc.marketStructure (per Phase 2Q's explicit
     * requirement not to couple them), but a future SMC layer can freely
     * consume both since each is a pure function of `candles` alone.
     *
     * ── Detection (candles c1=i-2, c2=i-1, c3=i) ─────────────────────────
     *   Bullish: Low(c3)  > High(c1)  -> zone [High(c1), Low(c3)]
     *   Bearish: High(c3) < Low(c1)   -> zone [High(c3), Low(c1)]
     * This is the 3-candle imbalance structure, NOT a same-bar
     * open-vs-prior-close session gap — c2's own candle is irrelevant to
     * the boundary math (it's what makes room for the imbalance) and is
     * deliberately not read here.
     *
     * ── Minimum size filter ──────────────────────────────────────────────
     * A candidate gap is discarded entirely (never created, not just
     * hidden) unless size (top-bottom) >= minSizeATRMultiplier *
     * ATR(atrLength) evaluated AT THE ORIGIN CANDLE (c1) — reuses Calc.atr
     * verbatim. Documented default: 0.1 * ATR(14), conservative enough to
     * only filter genuinely insignificant micro-gaps.
     *
     * ── Lifecycle / mitigation (deterministic, documented) ───────────────
     * Walking forward from c3+1, each FVG tracks a MONOTONIC high-water
     * mark `fillPercentage` (how deep price has intruded into the zone,
     * as a fraction of its size — bullish gaps fill from the top down via
     * subsequent lows, bearish gaps fill from the bottom up via
     * subsequent highs; once touched this can only grow, never shrink,
     * even if price retreats out of the zone again):
     *   - 'Touch' mode:    first candle with ANY intrusion -> immediately
     *                      'fully_mitigated' (spec: "mitigated when price
     *                      enters the zone").
     *   - 'Full Fill' mode: first intrusion -> 'partially_mitigated';
     *                      stays there (accumulating fillPercentage) until
     *                      the zone is completely traversed -> then
     *                      'fully_mitigated'.
     * `status`/`top`/`bottom`/`direction` never change once set — only
     * `fillPercentage`/`status` progress forward as genuinely NEW candles
     * are processed. That forward progression (active -> partially -> fully
     * mitigated as real future price action occurs) is the documented
     * lifecycle, not repainting: recomputing with MORE candles can advance
     * an existing FVG's lifecycle state (exactly like Calc.marketStructure's
     * broken-level tracking), but never move its boundaries or alter
     * history that already happened.
     * `mitigatedIndex`/`mitigatedAt` record exactly when a gap became
     * fully_mitigated — recorded, not discarded, precisely so a future
     * phase can look for "fully mitigated -> potential Inverse FVG"
     * without any rework of this detector (Phase 2Q's explicit IFVG
     * forward-compatibility requirement).
     *
     * ── Performance cap ───────────────────────────────────────────────────
     * Only the most recent `maxZones` gaps (default 50, documented,
     * configurable) are returned, keeping render cost bounded on very
     * long histories regardless of how many gaps a symbol has ever formed.
     *
     * Deliberate deviation from a strictly incremental engine: like
     * Calc.marketStructure, this is a pure, full-recompute-per-call
     * function — the same established, explicitly-reasoned tradeoff used
     * throughout this file (Supertrend, PSAR, Keltner, Market Structure),
     * not a persistent mutable streaming engine.
     */
    fvg: function (candles, minSizeATRMultiplier, atrLength, mitigationMode, maxZones) {
      var n = candles ? candles.length : 0;
      if (!candles || n < 3) return [];

      var minMult = parseFloat(minSizeATRMultiplier);
      if (isNaN(minMult) || minMult < 0) minMult = 0.1;
      var atrLen = parseInt(atrLength, 10);
      if (isNaN(atrLen) || atrLen < 1) atrLen = 14;
      var maxZ = parseInt(maxZones, 10);
      if (isNaN(maxZ) || maxZ < 1) maxZ = 50;
      var touchMode = (mitigationMode === 'Touch');

      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : Number(candles[i].close); }
      function Lo(i) { var v = Number(candles[i].low); return isFinite(v) ? v : Number(candles[i].close); }

      var atrVals = Calc.atr(candles, atrLen);
      var fvgs = [];

      for (var i = 2; i < n; i++) {
        var c1 = i - 2, c3 = i;
        var bullish = Lo(c3) > H(c1);
        var bearish = H(c3) < Lo(c1);
        if (!bullish && !bearish) continue;

        var top = bullish ? Lo(c3) : Lo(c1);
        var bottom = bullish ? H(c1) : H(c3);
        var size = top - bottom;
        if (!(size > 0)) continue;

        var atrHere = atrVals[c1];
        var minSize = (atrHere !== null && isFinite(atrHere)) ? minMult * atrHere : 0;
        if (size < minSize) continue;

        var fvg = {
          id: c1 + '-' + (bullish ? 'bullish' : 'bearish'),
          direction: bullish ? 'bullish' : 'bearish',
          originIndex: c1, confirmIndex: c3,
          startTime: candles[c1].time, confirmTime: candles[c3].time,
          top: top, bottom: bottom, midpoint: (top + bottom) / 2, size: size,
          status: 'active', fillPercentage: 0,
          mitigatedAt: null, mitigatedIndex: null, mitigationPrice: null
        };

        for (var j = c3 + 1; j < n; j++) {
          var extreme = bullish ? Lo(j) : H(j);
          var filled = bullish ? (top - extreme) : (extreme - bottom);
          if (filled < 0) filled = 0;
          if (filled > size) filled = size;
          var pct = filled / size;
          if (pct > fvg.fillPercentage) fvg.fillPercentage = pct;

          if (fvg.fillPercentage > 0 && fvg.status === 'active') {
            fvg.status = touchMode ? 'fully_mitigated' : 'partially_mitigated';
            if (touchMode) {
              fvg.mitigatedAt = candles[j].time;
              fvg.mitigatedIndex = j;
              fvg.mitigationPrice = extreme;
            }
          }
          if (!touchMode && fvg.fillPercentage >= 1) {
            fvg.status = 'fully_mitigated';
            fvg.mitigatedAt = candles[j].time;
            fvg.mitigatedIndex = j;
            fvg.mitigationPrice = extreme;
          }
          if (fvg.status === 'fully_mitigated') break;
        }

        fvgs.push(fvg);
      }

      if (fvgs.length > maxZ) fvgs = fvgs.slice(-maxZ);
      return fvgs;
    },

    /**
     * Order Blocks — a faithful port of TradingView's own reference "Order
     * Blocks" script (user-supplied Pine v5 source), NOT a Market-
     * Structure-event consumer like earlier phases planned. That switch
     * was deliberate: the original BOS/CHoCH-event-driven design produced
     * visibly MORE blocks than the reference script on identical data
     * (reported directly from a side-by-side chart comparison at the same
     * "Candle Range"/swingLength setting) — because Calc.marketStructure
     * fires on every confirmed swing break INCLUDING minor CHoCH pullbacks,
     * while the reference script only ever fires on one much stricter,
     * purely mechanical trigger (below). This rewrite reproduces that
     * exact trigger so block COUNT matches TradingView, not just the
     * lifecycle math.
     *
     * ── Running "leg" anchors (mirrors lastUp / lastDown / lastHigh / lastLow) ──
     * Two continuously-updated anchors are tracked candle-by-candle:
     *  - an "up-leg": reset to the current candle's own (Close, Low, High)
     *    every time a bullish candle (Close>Open) prints; its High then
     *    keeps extending (running max) across every subsequent candle
     *    until the NEXT bullish candle resets it. So a bearish OB's `top`
     *    is the peak of the whole impulse leg since the last up-candle,
     *    not merely that candle's own high — matching the reference
     *    script's box.new(top=lastHigh, bottom=lastUpLow) exactly.
     *  - a "down-leg": the mirror image, anchored on the last bearish
     *    candle, with a running-min Low extending until the next bearish
     *    candle resets it.
     *
     * ── Bearish OB creation (the ONLY independent trigger) ────────────────
     * A rolling `structureLow[i]` = the lowest Low over the swingLength
     * candles strictly BEFORE i (ta.lowest(low, swingLength)[1] — i itself
     * excluded, so this never looks at the candle it's being evaluated
     * against). A bearish Order Block is created exactly when Close
     * crosses UNDER that rolling low (Close[i-1] >= structureLow[i-1] AND
     * Close[i] < structureLow[i]) — a genuine new breakdown relative to
     * recent range, not a fractal-pivot swing break. Anchored on the
     * current up-leg; skipped if no bullish candle has printed yet, or the
     * up-leg is stale (>=1000 candles old), matching the reference
     * script's own guards.
     *
     * ── Bullish OB creation (deliberately NOT a symmetric trigger) ────────
     * The reference script has no independent "crossover a rolling high"
     * trigger. A bullish Order Block is created ONLY as the side effect of
     * an existing, not-yet-reclaimed bearish Order Block's own Close
     * crossing back above its `top` — i.e. the same decisive close-through
     * that (below) invalidates that bearish block simultaneously seeds a
     * fresh bullish block anchored on the current down-leg. This asymmetry
     * is intentional and reproduced exactly as given, not "fixed" to be
     * symmetric — it is exactly why the reference chart shows only a
     * handful of alternating zones instead of one per swing.
     *
     * ── Lifecycle (unchanged from the original design) ────────────────────
     * Once a candidate's origin/top/bottom/direction is established, it
     * walks forward with the exact same monotonic fillPercentage/Touch-vs-
     * Full-Fill mitigation and decisive-close invalidation loop used since
     * Phase 2R (bullish invalidated by Close<bottom, bearish by Close>top)
     * — this already matched the reference script's own box.delete
     * condition, so nothing here needed to change; only the CANDIDATE
     * GENERATION step above did.
     *
     * No displacement/ATR filter: the reference script has none — the
     * rolling-low crossunder IS the only qualification a bearish OB needs,
     * and a bullish OB's qualification is entirely inherited from the
     * bearish block it reclaims. Only the most recent maxZones blocks are
     * returned (default 50), same recency cap as every other engine here.
     */
    orderBlocks: function (candles, swingLength, mitigationMode, maxZones) {
      var n = candles ? candles.length : 0;
      if (!candles || n < 3) return [];

      var swingLen = parseInt(swingLength, 10);
      if (isNaN(swingLen) || swingLen < 1) swingLen = 5;
      var maxZ = parseInt(maxZones, 10);
      if (isNaN(maxZ) || maxZ < 1) maxZ = 50;
      var touchMode = (mitigationMode === 'Touch');

      function O(i) { var v = Number(candles[i].open); return isFinite(v) ? v : Number(candles[i].close); }
      function C(i) { var v = Number(candles[i].close); return isFinite(v) ? v : 0; }
      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : C(i); }
      function Lo(i) { var v = Number(candles[i].low); return isFinite(v) ? v : C(i); }

      // structureLow[i] = min(Low[i-swingLen .. i-1]) — a rolling window
      // strictly BEFORE i, only defined once swingLen prior candles exist.
      var structureLow = new Array(n).fill(null);
      for (var i = swingLen; i < n; i++) {
        var lo = Infinity;
        for (var k = i - swingLen; k < i; k++) { var v = Lo(k); if (v < lo) lo = v; }
        structureLow[i] = lo;
      }

      var lastUpIndex = -1, lastUpLow = null, lastHigh = null;
      var lastDownIndex = -1, lastDown = null, lastLow = null;
      var lastLongIndex = -1;
      var aliveBearish = [];
      var candidates = [];

      for (var idx = 0; idx < n; idx++) {
        if (idx > 0 && structureLow[idx] !== null && structureLow[idx - 1] !== null &&
            C(idx - 1) >= structureLow[idx - 1] && C(idx) < structureLow[idx] &&
            lastUpIndex !== -1 && (idx - lastUpIndex) < 1000) {
          var newTop = lastHigh, newBottom = lastUpLow;
          if (newTop > newBottom) {
            var bearishCand = { originIndex: lastUpIndex, confirmIndex: idx, direction: 'bearish', top: newTop, bottom: newBottom };
            candidates.push(bearishCand);
            aliveBearish.push(bearishCand);
          }
        }

        for (var b = aliveBearish.length - 1; b >= 0; b--) {
          if (C(idx) > aliveBearish[b].top) {
            aliveBearish.splice(b, 1);
            if (lastDownIndex !== -1 && (idx - lastDownIndex) < 1000 && idx > lastLongIndex) {
              var top2 = lastDown, bottom2 = lastLow;
              if (top2 > bottom2) {
                candidates.push({ originIndex: lastDownIndex, confirmIndex: idx, direction: 'bullish', top: top2, bottom: bottom2 });
                lastLongIndex = idx;
              }
            }
          }
        }

        if (C(idx) < O(idx)) { lastDown = H(idx); lastDownIndex = idx; lastLow = Lo(idx); }
        if (C(idx) > O(idx)) { lastUpIndex = idx; lastUpLow = Lo(idx); lastHigh = H(idx); }
        if (lastHigh !== null && H(idx) > lastHigh) lastHigh = H(idx);
        if (lastLow !== null && Lo(idx) < lastLow) lastLow = Lo(idx);
      }

      var obs = candidates.map(function (cand) {
        var bullish = cand.direction === 'bullish';
        var top = cand.top, bottom = cand.bottom;
        var ob = {
          id: cand.originIndex + '-' + cand.direction + '-' + cand.confirmIndex,
          direction: cand.direction,
          originIndex: cand.originIndex, confirmIndex: cand.confirmIndex,
          startTime: candles[cand.originIndex].time, confirmTime: candles[cand.confirmIndex].time,
          top: top, bottom: bottom, midpoint: (top + bottom) / 2, size: top - bottom,
          // The origin candle's OWN High/Low — top/bottom above can extend
          // beyond this (top tracks the running peak of the whole up-leg
          // for a bearish block, bottom the running trough of the whole
          // down-leg for a bullish one; see this function's docstring), so
          // originHigh/originLow mark exactly which slice of the box is
          // the actual anchor candle vs. the leg's later extension —
          // consumed by the renderer to draw the origin candle's own
          // range as a visibly darker "core" band within the fuller zone.
          originHigh: H(cand.originIndex), originLow: Lo(cand.originIndex),
          triggerEvent: bullish ? 'bullishReclaim' : 'bearishBreakdown',
          status: 'active', fillPercentage: 0,
          mitigatedAt: null, mitigatedIndex: null, mitigationPrice: null,
          invalidatedAt: null, invalidatedIndex: null
        };

        for (var j = cand.confirmIndex + 1; j < n; j++) {
          if (bullish && C(j) < bottom) {
            ob.status = 'invalidated'; ob.invalidatedAt = candles[j].time; ob.invalidatedIndex = j;
            break;
          }
          if (!bullish && C(j) > top) {
            ob.status = 'invalidated'; ob.invalidatedAt = candles[j].time; ob.invalidatedIndex = j;
            break;
          }

          var extreme = bullish ? Lo(j) : H(j);
          var filled = bullish ? (top - extreme) : (extreme - bottom);
          if (filled < 0) filled = 0;
          if (filled > ob.size) filled = ob.size;
          var pct = ob.size > 0 ? filled / ob.size : 0;
          if (pct > ob.fillPercentage) ob.fillPercentage = pct;

          if (ob.fillPercentage > 0 && ob.status === 'active') {
            ob.status = touchMode ? 'fully_mitigated' : 'partially_mitigated';
            if (touchMode) { ob.mitigatedAt = candles[j].time; ob.mitigatedIndex = j; ob.mitigationPrice = extreme; }
          }
          if (!touchMode && ob.fillPercentage >= 1) {
            ob.status = 'fully_mitigated'; ob.mitigatedAt = candles[j].time; ob.mitigatedIndex = j; ob.mitigationPrice = extreme;
          }
          if (ob.status === 'fully_mitigated') break;
        }

        return ob;
      });

      obs.sort(function (a, b) { return a.originIndex - b.originIndex; });
      if (obs.length > maxZ) obs = obs.slice(-maxZ);
      return obs;
    },

    /**
     * Liquidity — Equal Highs/Lows and Swing Liquidity, built ENTIRELY from
     * Calc.marketStructure's confirmed `swings` (never redetects pivots).
     *
     * ── Two views of the same swings, independently toggleable ──────────
     * Every confirmed swing becomes its own SWING_HIGH/SWING_LOW pool
     * (touchCount=1) — "Swing Liquidity". Separately, swings of the same
     * type are clustered by price proximity; any cluster with >=
     * minTouches members becomes an EQUAL_HIGH/EQUAL_LOW pool spanning all
     * its members' touch points. These are two different classifications
     * of the same underlying data, not mutually exclusive — matching the
     * spec's independent "Equal Highs" / "Swing High Liquidity" toggles.
     *
     * ── Tolerance ─────────────────────────────────────────────────────────
     * ATR-relative (this codebase's established relative-threshold
     * mechanism, already used by Keltner/Market Structure/FVG/Order
     * Blocks): tolerance = toleranceATRMultiplier * ATR(atrLength),
     * evaluated once using the FULL series' ATR at each swing's own index
     * (so tolerance can vary slightly per era of the chart, not a single
     * global constant) — reuses Calc.atr verbatim.
     *
     * ── Deterministic clustering (documented) ────────────────────────────
     * Swings of one type are sorted by price ascending. A cluster starts
     * at the first swing not yet assigned; every subsequent swing joins
     * the CURRENT cluster only if within tolerance of that cluster's
     * FIRST (anchor) member's price — not merely the previous member —
     * bounding a cluster's total spread to 2*tolerance and avoiding
     * unbounded "chaining". A swing failing that check starts a new
     * cluster. This is a single fixed rule, identical for highs and lows.
     *
     * ── Side classification ──────────────────────────────────────────────
     * EQUAL_HIGH/SWING_HIGH -> BUY_SIDE (resting buy-stops/liquidity above
     * resistance). EQUAL_LOW/SWING_LOW -> SELL_SIDE (resting sell-stops
     * below support).
     *
     * ── Non-repainting ────────────────────────────────────────────────────
     * Inherited for free: pools are built only from swings that
     * Calc.marketStructure already confirmed (its own confirmedAt gating),
     * so a pool's identity/price/touchPoints never change once formed —
     * only NEW swings can extend an existing cluster going forward.
     *
     * ── Phase 2T (Liquidity Sweeps) readiness ────────────────────────────
     * Each pool carries `swept`/`sweptAt`/`sweptPrice`, always
     * null/false here by design — this phase deliberately does not
     * classify sweeps (per spec), it only reserves the fields so a future
     * sweep engine can populate them without touching this detector.
     *
     * Only the most recent maxPools pools are returned (default 50,
     * documented, configurable). Same full-recompute-per-call deviation
     * as every other engine in this file.
     */
    liquidity: function (candles, swingLength, toleranceATRMultiplier, atrLength, minTouches, maxPools) {
      var n = candles ? candles.length : 0;
      if (!candles || n === 0) return [];

      var swingLen = parseInt(swingLength, 10);
      if (isNaN(swingLen) || swingLen < 1) swingLen = 5;
      var tolMult = parseFloat(toleranceATRMultiplier);
      if (isNaN(tolMult) || tolMult < 0) tolMult = 0.5;
      var atrLen = parseInt(atrLength, 10);
      if (isNaN(atrLen) || atrLen < 1) atrLen = 14;
      var minT = parseInt(minTouches, 10);
      if (isNaN(minT) || minT < 2) minT = 2;
      var maxP = parseInt(maxPools, 10);
      if (isNaN(maxP) || maxP < 1) maxP = 50;

      var msResult = Calc.marketStructure(candles, swingLen, 'Close', 1.5, atrLen);
      var atrVals = Calc.atr(candles, atrLen);
      var pools = [];

      function toleranceAt(idx) {
        var a = atrVals[idx];
        var c = Number(candles[idx] ? (candles[idx].close || candles[idx].high) : 0);
        if (a !== null && isFinite(a) && a > 0) {
          if (tolMult > 0.1) return tolMult * a;
          return Math.max(tolMult * a, (tolMult * 4) * a, 0.0006 * c);
        }
        if (idx > 0 && candles[idx]) {
          var prevC = Number(candles[idx - 1].close);
          var curH = Number(candles[idx].high || candles[idx].close);
          var curL = Number(candles[idx].low || candles[idx].close);
          var tr = Math.max(curH - curL, Math.abs(curH - prevC), Math.abs(curL - prevC));
          if (isFinite(tr) && tr > 0) {
            if (tolMult > 0.1) return tolMult * tr;
            return Math.max(tolMult * tr, (tolMult * 4) * tr, 0.0006 * c);
          }
        }
        return (c > 0) ? Math.max(tolMult * 0.05 * c, 0.0006 * c) : 0;
      }

      function buildPools(swingType, poolType, side) {
        var swings = msResult.swings.filter(function (s) { return s.type === swingType; });

        // Swing Liquidity: every confirmed swing is its own single-touch pool.
        swings.forEach(function (s) {
          pools.push({
            id: 'swing-' + s.index + '-' + poolType,
            type: swingType === 'high' ? 'SWING_HIGH' : 'SWING_LOW',
            side: side,
            price: s.price,
            tolerance: toleranceAt(s.index),
            touchCount: 1,
            touchPoints: [{ index: s.index, time: s.time, price: s.price }],
            firstTouchTime: s.time, latestTouchTime: s.time,
            firstTouchIndex: s.index, latestTouchIndex: s.index,
            status: 'active', swept: false, sweptAt: null, sweptPrice: null
          });
        });

        // Equal Highs/Lows: cluster by price proximity (anchor-based, see
        // docstring), keep clusters with >= minTouches members.
        var sorted = swings.slice().sort(function (a, b) { return a.price - b.price; });
        var clusters = [];
        var current = null;
        sorted.forEach(function (s) {
          var tol = toleranceAt(s.index);
          if (current && Math.abs(s.price - current.anchorPrice) <= Math.max(tol, current.anchorTolerance)) {
            current.members.push(s);
          } else {
            current = { anchorPrice: s.price, anchorTolerance: tol, members: [s] };
            clusters.push(current);
          }
        });

        clusters.forEach(function (cl) {
          if (cl.members.length < minT) return;
          var members = cl.members.slice().sort(function (a, b) { return a.index - b.index; });
          var avgPrice = members.reduce(function (sum, m) { return sum + m.price; }, 0) / members.length;
          pools.push({
            id: 'eq-' + poolType + '-' + members[0].index + '-' + members[members.length - 1].index,
            type: poolType,
            side: side,
            price: avgPrice,
            tolerance: cl.anchorTolerance,
            touchCount: members.length,
            touchPoints: members.map(function (m) { return { index: m.index, time: m.time, price: m.price }; }),
            firstTouchTime: members[0].time, latestTouchTime: members[members.length - 1].time,
            firstTouchIndex: members[0].index, latestTouchIndex: members[members.length - 1].index,
            status: 'active', swept: false, sweptAt: null, sweptPrice: null
          });
        });
      }

      buildPools('high', 'EQUAL_HIGH', 'BUY_SIDE');
      buildPools('low', 'EQUAL_LOW', 'SELL_SIDE');

      // Sorted by index, not time: candle time can be a string, a number,
      // or (in minimal test fixtures) absent entirely, so latestTouchIndex
      // — always a plain, always-present integer — is the robust and
      // semantically equivalent "most recent" ordering key.
      pools.sort(function (a, b) { return a.latestTouchIndex - b.latestTouchIndex; });
      if (pools.length > maxP) pools = pools.slice(-maxP);
      return pools;
    },

    /**
     * Liquidity Sweeps — consumes Calc.liquidity's pools directly (never
     * rediscovers EQH/EQL/swing liquidity). Preserves the original pool
     * type (EQUAL_HIGH/EQUAL_LOW/SWING_HIGH/SWING_LOW) inside each event.
     *
     * ── Take vs. confirm (distinguishes a sweep from a breakout) ────────
     * For a BUY_SIDE pool at `price`: "taken" the first candle whose High
     * > price. From there, walk forward checking for confirmation:
     *   'Close Rejection' (default): a candle's Close < price.
     *   'Wick + Reclaim': a candle's Low < price (intrabar reclaim is
     *      enough — faster/noisier than Close Rejection, which is why it
     *      is NOT the default).
     * Both checks run on the SAME candle that took liquidity too, so a
     * single-candle grab-and-reject is confirmed immediately; a
     * multi-candle sweep (take on candle A, reclaim on a later candle B)
     * is equally supported — the "taken" state simply persists forward
     * until a candle satisfies the confirmation rule. If a candle takes
     * liquidity and CLOSES beyond the level without ever reclaiming
     * within the available data, no event is ever emitted for that
     * instance — this is Scenario B (potential breakout) from the spec,
     * and an unconfirmed candidate is never displayed as a confirmed
     * sweep. SELL_SIDE mirrors this exactly with Low/High swapped.
     *
     * ── One sweep per pool ────────────────────────────────────────────────
     * The forward walk STOPS the instant a pool's sweep confirms — a
     * pool can never produce a second event.
     *
     * ── Market Structure context — deliberately NOT computed here ───────
     * Calc.liquidity already calls Calc.marketStructure once internally;
     * calling it again here just to cross-reference BOS/CHoCH/MSS would
     * duplicate that calculation, which the spec explicitly forbids. Each
     * event instead exposes its raw sweepIndex/reclaimIndex/liquidityPrice
     * so a future SMC orchestrator — which will already hold its own
     * Market Structure result — can correlate a sweep against BOS/CHoCH/
     * MSS itself without this engine recomputing anything.
     *
     * ── Non-repainting ────────────────────────────────────────────────────
     * Inherited: pools themselves are already confirmation-gated by
     * Calc.marketStructure/Calc.liquidity, the sweep walk only reads
     * candles AFTER a pool's own latestTouchIndex, and an event is only
     * emitted once its confirmation rule is actually satisfied — never
     * speculatively for a still-pending candidate.
     *
     * Only the most recent maxEvents sweeps are returned (default 50,
     * documented, configurable). Same full-recompute-per-call deviation
     * as every other engine in this file.
     */
    liquiditySweeps: function (candles, swingLength, toleranceATRMultiplier, atrLength, minTouches, confirmationMode, maxEvents) {
      var n = candles ? candles.length : 0;
      if (!candles || n === 0) return [];

      var maxEv = parseInt(maxEvents, 10);
      if (isNaN(maxEv) || maxEv < 1) maxEv = 50;
      var wickReclaim = (confirmationMode === 'Wick + Reclaim');

      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : Number(candles[i].close); }
      function Lo(i) { var v = Number(candles[i].low); return isFinite(v) ? v : Number(candles[i].close); }
      function C(i) { var v = Number(candles[i].close); return isFinite(v) ? v : 0; }

      var pools = Calc.liquidity(candles, swingLength, toleranceATRMultiplier, atrLength, minTouches, 1000);
      var sweeps = [];

      pools.forEach(function (pool) {
        var buySide = pool.side === 'BUY_SIDE';
        var startIdx = pool.latestTouchIndex + 1;
        var taken = false, takenIndex = -1, sweepExtreme = null;

        for (var j = startIdx; j < n; j++) {
          if (!taken) {
            var breaches = buySide ? (H(j) > pool.price) : (Lo(j) < pool.price);
            if (!breaches) continue;
            taken = true;
            takenIndex = j;
            sweepExtreme = buySide ? H(j) : Lo(j);
          } else {
            if (j > takenIndex + 5) break;
            sweepExtreme = buySide ? Math.max(sweepExtreme, H(j)) : Math.min(sweepExtreme, Lo(j));
          }

          var confirmed = buySide
            ? (wickReclaim ? Lo(j) < pool.price : C(j) < pool.price)
            : (wickReclaim ? H(j) > pool.price : C(j) > pool.price);

          if (confirmed) {
            var sweepDepth = buySide ? (sweepExtreme - pool.price) : (pool.price - sweepExtreme);
            sweeps.push({
              id: pool.id + '-sweep-' + takenIndex,
              direction: buySide ? 'bearish' : 'bullish',
              sweepType: buySide ? 'BUY_SIDE_SWEEP' : 'SELL_SIDE_SWEEP',
              liquidityPoolId: pool.id,
              liquidityPoolType: pool.type,
              liquidityPrice: pool.price,
              anchorIndex: pool.firstTouchIndex,
              anchorTime: pool.firstTouchTime,
              sweepIndex: takenIndex, sweepTime: candles[takenIndex].time,
              sweepExtreme: sweepExtreme, sweepDepth: sweepDepth,
              reclaimIndex: j, reclaimTime: candles[j].time, reclaimPrice: C(j),
              confirmationType: wickReclaim ? 'Wick + Reclaim' : 'Close Rejection',
              status: 'confirmed'
            });
            break;
          }
        }
      });

      sweeps.sort(function (a, b) { return a.sweepIndex - b.sweepIndex; });
      if (sweeps.length > maxEv) sweeps = sweeps.slice(-maxEv);
      return sweeps;
    },

    /**
     * Premium & Discount — dealing range derived directly from
     * Calc.marketStructure's confirmed swings (never a second swing
     * detector). The active range is the most recently confirmed swing
     * high paired with the most recently confirmed swing low, using the
     * exact same "most-recent-of-each-type" reveal mechanism Market
     * Structure itself uses for activeHigh/activeLow — so the range only
     * changes at a genuine new-swing-confirmation point, never on an
     * ordinary candle in between (satisfies "do not continuously change
     * the range on every candle").
     *
     * rangeHigh/rangeLow are always Math.max/min of the two swing prices
     * — never simply "the swing typed as high" / "the swing typed as
     * low" — so a chronologically bearish leg (swing high formed BEFORE
     * the swing low) still produces mathematically correct ordering
     * exactly as the spec requires. `direction` records which leg it
     * actually was ('bullish' if the low came first, 'bearish' if the
     * high came first) purely as descriptive metadata.
     *
     * equilibrium = (rangeHigh + rangeLow) / 2. Each time the pair
     * changes, a NEW immutable DealingRange record is appended (never
     * mutating an earlier one) and the previous record's `active` flag is
     * cleared — giving a full, non-repainting range history for free.
     *
     * currentClassification compares the LAST candle's close against the
     * ACTIVE range's equilibrium (exact-match epsilon of 1e-9, documented
     * rather than an arbitrary unstated tolerance): PREMIUM above,
     * DISCOUNT below, EQUILIBRIUM within epsilon.
     */
    premiumDiscount: function (candles, swingLength) {
      var n = candles ? candles.length : 0;
      if (!candles || n === 0) return { ranges: [], currentClassification: null, macroRange: null };

      var swingLen = parseInt(swingLength, 10);
      if (isNaN(swingLen) || swingLen < 1) swingLen = 5;

      var msResult = Calc.marketStructure(candles, swingLen, 'Close', 1.5, 14);
      var swings = msResult.swings;

      function calcRangeVolume(startIdx, endIdx, eqPrice) {
        var buyVol = 0, sellVol = 0, totalVol = 0;
        var premBuy = 0, premSell = 0, premTotal = 0;
        var discBuy = 0, discSell = 0, discTotal = 0;
        var s = Math.max(0, Math.min(n - 1, startIdx));
        var e = Math.max(0, Math.min(n - 1, endIdx));
        if (s > e) { var tmp = s; s = e; e = tmp; }
        for (var k = s; k <= e; k++) {
          var c = candles[k];
          var v = Number(c.volume) || 0;
          var isBull = Number(c.close) >= Number(c.open);
          if (isBull) buyVol += v; else sellVol += v;
          totalVol += v;
          if (eqPrice !== undefined && eqPrice !== null) {
            if (Number(c.close) >= eqPrice) {
              if (isBull) premBuy += v; else premSell += v;
              premTotal += v;
            } else {
              if (isBull) discBuy += v; else discSell += v;
              discTotal += v;
            }
          }
        }
        var delta = buyVol - sellVol;
        var deltaPct = totalVol > 0 ? (delta / totalVol) * 100 : 0;
        return {
          buyVolume: buyVol,
          sellVolume: sellVol,
          totalVolume: totalVol,
          deltaVolume: delta,
          deltaVolumePct: deltaPct,
          premiumBuyVol: premBuy,
          premiumSellVol: premSell,
          premiumDelta: premBuy - premSell,
          premiumTotalVol: premTotal,
          discountBuyVol: discBuy,
          discountSellVol: discSell,
          discountDelta: discBuy - discSell,
          discountTotalVol: discTotal
        };
      }

      var ranges = [];
      var lastHigh = null, lastLow = null;
      var revealPtr = 0;

      for (var i = 0; i < n; i++) {
        var changed = false;
        while (revealPtr < swings.length && swings[revealPtr].confirmedAt <= i) {
          var sw = swings[revealPtr];
          if (sw.type === 'high') { lastHigh = sw; changed = true; }
          else { lastLow = sw; changed = true; }
          revealPtr++;
        }
        if (changed && lastHigh && lastLow) {
          var rangeHigh = Math.max(lastHigh.price, lastLow.price);
          var rangeLow = Math.min(lastHigh.price, lastLow.price);
          var eq = (rangeHigh + rangeLow) / 2;
          var startIdx = Math.min(lastHigh.index, lastLow.index);
          var endIdx = Math.max(lastHigh.index, lastLow.index);
          var volMetrics = calcRangeVolume(startIdx, endIdx, eq);
          var newRange = {
            id: lastLow.index + '-' + lastHigh.index,
            direction: (lastLow.index < lastHigh.index) ? 'bullish' : 'bearish',
            rangeHigh: rangeHigh, rangeLow: rangeLow, equilibrium: eq,
            highIndex: lastHigh.index, lowIndex: lastLow.index,
            highTime: lastHigh.time, lowTime: lastLow.time,
            sourceStructure: { highClassification: lastHigh.classification, lowClassification: lastLow.classification },
            createdIndex: i, createdTime: candles[i].time,
            active: true,
            buyVolume: volMetrics.buyVolume,
            sellVolume: volMetrics.sellVolume,
            totalVolume: volMetrics.totalVolume,
            deltaVolume: volMetrics.deltaVolume,
            deltaVolumePct: volMetrics.deltaVolumePct,
            premiumBuyVol: volMetrics.premiumBuyVol,
            premiumSellVol: volMetrics.premiumSellVol,
            premiumDelta: volMetrics.premiumDelta,
            discountBuyVol: volMetrics.discountBuyVol,
            discountSellVol: volMetrics.discountSellVol,
            discountDelta: volMetrics.discountDelta
          };
          var prevRange = ranges.length ? ranges[ranges.length - 1] : null;
          if (!prevRange || prevRange.id !== newRange.id) {
            if (prevRange) prevRange.active = false;
            ranges.push(newRange);
          }
        }
      }

      var active = ranges.length ? ranges[ranges.length - 1] : null;
      var currentClassification = null;
      var activeVolMetrics = null;
      if (active) {
        var lastClose = Number(candles[n - 1].close);
        if (isFinite(lastClose)) {
          if (Math.abs(lastClose - active.equilibrium) < 1e-9) currentClassification = 'EQUILIBRIUM';
          else currentClassification = lastClose > active.equilibrium ? 'PREMIUM' : 'DISCOUNT';
        }
        activeVolMetrics = calcRangeVolume(Math.min(active.highIndex, active.lowIndex), n - 1, active.equilibrium);
      }

      var macroRange = null;
      if (swings.length >= 2) {
        var highSwings = swings.filter(function (s) { return s.type === 'high'; });
        var lowSwings = swings.filter(function (s) { return s.type === 'low'; });
        if (highSwings.length > 0 && lowSwings.length > 0) {
          var mHigh = highSwings[0];
          for (var h = 1; h < highSwings.length; h++) {
            if (highSwings[h].price >= mHigh.price) mHigh = highSwings[h];
          }
          var mLow = lowSwings[0];
          for (var l = 1; l < lowSwings.length; l++) {
            if (lowSwings[l].price <= mLow.price) mLow = lowSwings[l];
          }
          var mHighPrice = mHigh.price;
          var mLowPrice = mLow.price;
          var mEq = (mHighPrice + mLowPrice) / 2;
          var mVol = calcRangeVolume(Math.min(mHigh.index, mLow.index), n - 1, mEq);
          var mHighVol = calcRangeVolume(mHigh.index, n - 1, mEq);
          var mLowVol = calcRangeVolume(mLow.index, n - 1, mEq);
          macroRange = {
            rangeHigh: mHighPrice,
            rangeLow: mLowPrice,
            equilibrium: mEq,
            highIndex: mHigh.index,
            lowIndex: mLow.index,
            highTime: mHigh.time,
            lowTime: mLow.time,
            highDelta: mHighVol.deltaVolume,
            lowDelta: mLowVol.deltaVolume,
            deltaVolume: mVol.deltaVolume,
            deltaVolumePct: mVol.deltaVolumePct,
            totalVolume: mVol.totalVolume
          };
        }
      }

      return { ranges: ranges, currentClassification: currentClassification, macroRange: macroRange, activeVolume: activeVolMetrics };
    },

    /**
     * SMC Setups — the orchestration layer. Detects NOTHING new itself;
     * it is a pure consumer/correlator over the six existing engines
     * (Calc.marketStructure, Calc.liquidity, Calc.liquiditySweeps,
     * Calc.fvg, Calc.orderBlocks, Calc.premiumDiscount), flagging
     * confluence using a standard, documented SMC playbook rather than an
     * invented signal:
     *
     *   Liquidity Sweep (2T)
     *         +
     *   Market Structure shift — CHoCH or MSS, SAME direction as the
     *   sweep's implied reversal, confirming within structureWindowBars
     *   candles after the sweep's reclaim (2P)
     *         =
     *   Candidate SMC Setup (both of the above are REQUIRED — a sweep
     *   with no follow-through structure shift never becomes a Setup)
     *
     * Then, purely as ADDITIONAL, OPTIONAL context (never required,
     * matching every prior phase's explicit instruction not to force
     * every sweep/FVG/OB to depend on the others):
     *   - premiumDiscountAligned: is price on the "cheap"/"expensive" side
     *     of the active dealing range at the structure event (bullish
     *     setup + DISCOUNT, or bearish setup + PREMIUM) (2U)?
     *   - fvgConfluence: does a same-direction FVG confirm within the
     *     same window (2Q)?
     *   - obConfluence: does a same-direction Order Block confirm within
     *     the same window (2R)?
     *
     * `score` is a PLAIN COUNT of satisfied boolean conditions — 2
     * (sweep+structure, always both true for any Setup to exist) plus up
     * to 3 more (premium/discount, FVG, OB) — never a weighted or
     * mysterious formula. This directly follows Order Blocks' and
     * Liquidity's own explicit "no arbitrary black-box scoring" rule.
     *
     * ── Timing window (documented, bounded, deterministic) ──────────────
     * "Nearby" is defined as: structureEvent.breakIndex falls in
     * [sweep.reclaimIndex, sweep.reclaimIndex + structureWindowBars]; FVG/
     * OB confluence uses [sweep.sweepIndex, structureEvent.breakIndex +
     * structureWindowBars]. No unbounded lookahead scanning.
     *
     * ── Duplicate Market Structure/sub-engine computation ────────────────
     * This function calls Calc.marketStructure, Calc.liquiditySweeps
     * (which itself calls Calc.liquidity, which itself calls
     * Calc.marketStructure again), Calc.fvg, Calc.orderBlocks (which
     * calls Calc.marketStructure again), and Calc.premiumDiscount (which
     * calls Calc.marketStructure again) — i.e. Market Structure is
     * genuinely recomputed multiple times per render. This is the SAME
     * deliberate, already-documented tradeoff every composite engine in
     * this file has made since Phase 2R (reuse the FUNCTION, not a
     * cross-indicator shared-computation cache that doesn't exist
     * anywhere in this codebase) — changing it would mean altering the
     * signatures of five already-shipped, already-tested functions,
     * which is out of scope for adding one more indicator on top.
     *
     * At most ONE Setup per sweep (a sweep either finds a qualifying
     * structure event or it doesn't); only the most recent maxSetups are
     * returned (default 20 — deliberately smaller than the other engines'
     * defaults, since a confirmed Setup is a rarer, higher-value
     * composite event, not a raw per-candle signal).
     */
    smcSetups: function (candles, swingLength, toleranceATRMultiplier, atrLength, minTouches, mitigationMode, confirmationMode, structureWindowBars, minScore, maxSetups) {
      var n = candles ? candles.length : 0;
      if (!candles || n === 0) return [];

      var winBars = parseInt(structureWindowBars, 10);
      if (isNaN(winBars) || winBars < 1) winBars = 10;
      var minSc = parseInt(minScore, 10);
      if (isNaN(minSc) || minSc < 2) minSc = 2;
      var maxS = parseInt(maxSetups, 10);
      if (isNaN(maxS) || maxS < 1) maxS = 20;

      var msResult = Calc.marketStructure(candles, swingLength, 'Close', 1.5, atrLength);
      var sweeps = Calc.liquiditySweeps(candles, swingLength, toleranceATRMultiplier, atrLength, minTouches, confirmationMode, 1000);
      var fvgs = Calc.fvg(candles, 0.1, atrLength, mitigationMode, 1000);
      var obs = Calc.orderBlocks(candles, swingLength, mitigationMode, 1000);
      var pdResult = Calc.premiumDiscount(candles, swingLength);

      function classificationAt(index) {
        var active = null;
        for (var i = 0; i < pdResult.ranges.length; i++) {
          if (pdResult.ranges[i].createdIndex <= index) active = pdResult.ranges[i];
          else break;
        }
        if (!active) return null;
        var price = Number(candles[index].close);
        if (!isFinite(price)) return null;
        if (Math.abs(price - active.equilibrium) < 1e-9) return 'EQUILIBRIUM';
        return price > active.equilibrium ? 'PREMIUM' : 'DISCOUNT';
      }

      var setups = [];
      sweeps.forEach(function (sweep) {
        var structureEvent = null;
        for (var i = 0; i < msResult.events.length; i++) {
          var ev = msResult.events[i];
          if (ev.direction !== sweep.direction) continue;
          if (ev.type !== 'CHOCH' && ev.type !== 'MSS') continue;
          if (ev.breakIndex >= sweep.reclaimIndex && ev.breakIndex <= sweep.reclaimIndex + winBars) {
            structureEvent = ev;
            break;
          }
        }
        if (!structureEvent) return;

        var pdContext = classificationAt(structureEvent.breakIndex);
        var pdAligned = (sweep.direction === 'bullish' && pdContext === 'DISCOUNT') ||
                        (sweep.direction === 'bearish' && pdContext === 'PREMIUM');

        var windowStart = sweep.sweepIndex;
        var windowEnd = structureEvent.breakIndex + winBars;

        var fvgMatch = null;
        for (var j = 0; j < fvgs.length; j++) {
          if (fvgs[j].direction === sweep.direction && fvgs[j].confirmIndex >= windowStart && fvgs[j].confirmIndex <= windowEnd) {
            fvgMatch = fvgs[j];
            break;
          }
        }
        var obMatch = null;
        for (var k = 0; k < obs.length; k++) {
          if (obs[k].direction === sweep.direction && obs[k].confirmIndex >= windowStart && obs[k].confirmIndex <= windowEnd) {
            obMatch = obs[k];
            break;
          }
        }

        var score = 2 + (pdAligned ? 1 : 0) + (fvgMatch ? 1 : 0) + (obMatch ? 1 : 0);
        if (score < minSc) return;

        setups.push({
          id: sweep.id + '-setup-' + structureEvent.breakIndex,
          direction: sweep.direction,
          sweepIndex: sweep.sweepIndex, reclaimIndex: sweep.reclaimIndex,
          liquidityPrice: sweep.liquidityPrice, liquidityPoolType: sweep.liquidityPoolType,
          structureEvent: { type: structureEvent.type, breakIndex: structureEvent.breakIndex, breakTime: structureEvent.breakTime },
          premiumDiscountContext: pdContext, premiumDiscountAligned: pdAligned,
          fvgConfluence: !!fvgMatch, fvgConfluenceId: fvgMatch ? fvgMatch.id : null,
          obConfluence: !!obMatch, obConfluenceId: obMatch ? obMatch.id : null,
          score: score,
          index: structureEvent.breakIndex, time: candles[structureEvent.breakIndex].time
        });
      });

      setups.sort(function (a, b) { return a.index - b.index; });
      if (setups.length > maxS) setups = setups.slice(-maxS);
      return setups;
    },

    /**
     * Breaker & Mitigation Blocks — consumes Calc.orderBlocks directly
     * (ONE internal call supplies both outcomes below; Order Block
     * detection is never recalculated or reimplemented here).
     *
     * ── What invalidates the source OB? (reused, not redefined) ──────────
     * Exactly Calc.orderBlocks' own existing invalidation rule: a candle
     * CLOSING beyond the OPPOSITE boundary (bullish OB: Close < bottom;
     * bearish OB: Close > top) — already a decisive, structural-confirmation-
     * grade condition, not a bare wick touch. This engine calls
     * Calc.orderBlocks with mitigation FORCED to 'Full Fill' internally
     * (regardless of any separately-configured OB indicator elsewhere),
     * because Mitigation Block qualification below must never be based on
     * a mere Touch (see spec: "do not treat a simple touch as
     * automatically confirmed mitigation").
     *
     * ── Breaker creation & confirmation ──────────────────────────────────
     * Every OB with status 'invalidated' becomes the SOURCE of exactly one
     * Breaker. The zone (top/bottom) is the identical OB zone — only the
     * ROLE flips: an invalidated bullish OB (former support) becomes a
     * BEARISH_BREAKER (resistance); an invalidated bearish OB becomes a
     * BULLISH_BREAKER (support). No separate "structural confirmation"
     * step is required — the OB's own invalidation (a decisive close
     * through it) IS the Breaker's confirmation event, so
     * confirmationIndex == the source OB's invalidatedIndex. BOS/CHoCH/MSS
     * are NOT required for a Breaker to form (per spec section 6); the
     * source OB's own `triggerEvent` is carried over as context only.
     *
     * ── Breaker's own subsequent lifecycle ───────────────────────────────
     * From invalidatedIndex+1 onward, a Breaker is walked forward exactly
     * like a FRESH Order Block of its NEW (flipped) direction, seeded at
     * the pre-existing zone: a BEARISH_BREAKER tracks subsequent Highs for
     * mitigation and a Close above `top` for its own re-invalidation
     * (mirrors a bearish OB's math exactly); a BULLISH_BREAKER tracks Lows
     * and a Close below `bottom` (mirrors a bullish OB). Same monotonic
     * fill-percentage / Touch-vs-Full-Fill mechanics as every mitigation
     * walk in this file. This intentionally duplicates that small loop
     * shape rather than extracting a shared helper — the same choice
     * already made independently by Calc.fvg and Calc.orderBlocks, kept
     * consistent here rather than refactoring three shipped, tested
     * functions to share one.
     *
     * ── Mitigation Blocks (a STRICTLY different concept, never OB renamed) ──
     * Every OB with status 'fully_mitigated' (and NEVER invalidated —
     * these are mutually exclusive by construction, satisfying "no
     * ambiguous logic where the same event is classified as both")
     * becomes a Mitigation Block: a VALID structural zone that price
     * fully revisited and reacted to, preserving its ORIGINAL direction
     * (unlike a Breaker, nothing flips). It carries no further lifecycle
     * of its own — it is a completed historical fact, not an ongoing
     * zone — and "expires" only via the same maxBlocks recency cap every
     * other engine in this file uses (a deliberately simple, documented
     * expiration rule, not a second timer/age system).
     *
     * ── Optional context (never required, never recomputed) ─────────────
     * sourceStructureEvent carries over the source OB's own `triggerEvent`
     * (from Calc.marketStructure, already computed inside Calc.orderBlocks
     * — not recomputed here). Liquidity Sweep / Premium-Discount / FVG
     * correlation is deliberately NOT computed inside this engine (the
     * same reasoning Calc.smcSetups documents for itself): each Breaker/
     * Mitigation object exposes its own zone/index/direction so a future
     * SMC orchestrator — which already holds those other engines' results
     * — can correlate them without this engine recomputing anything.
     *
     * Non-repainting is inherited: Calc.orderBlocks' own statuses only
     * progress forward as genuinely new candles arrive, and this engine's
     * own forward walk uses the identical decisive-close-first,
     * monotonic-fill pattern. Same full-recompute-per-call deviation as
     * every other engine in this file.
     */
    breakerMitigation: function (candles, swingLength, breakerMitigationMode, maxBlocks) {
      var n = candles ? candles.length : 0;
      if (!candles || n === 0) return { breakers: [], mitigations: [] };

      var maxB = parseInt(maxBlocks, 10);
      if (isNaN(maxB) || maxB < 1) maxB = 50;
      var touchMode = (breakerMitigationMode === 'Touch');

      function O(i) { var v = Number(candles[i].open); return isFinite(v) ? v : Number(candles[i].close); }
      function C(i) { var v = Number(candles[i].close); return isFinite(v) ? v : 0; }
      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : C(i); }
      function Lo(i) { var v = Number(candles[i].low); return isFinite(v) ? v : C(i); }

      // Forced 'Full Fill' — see docstring: Mitigation Block qualification
      // must never rest on a mere Touch, regardless of any separately-
      // configured OB indicator's own mitigation setting elsewhere.
      var obs = Calc.orderBlocks(candles, swingLength, 'Full Fill', 1000);

      var breakers = [];
      var mitigations = [];

      obs.forEach(function (ob) {
        if (ob.status === 'invalidated') {
          var bullishBreaker = (ob.direction === 'bearish'); // flipped role
          var top = ob.top, bottom = ob.bottom, size = ob.size;
          var breaker = {
            id: ob.id + '-breaker',
            sourceOrderBlockId: ob.id,
            direction: bullishBreaker ? 'BULLISH_BREAKER' : 'BEARISH_BREAKER',
            top: top, bottom: bottom, midpoint: (top + bottom) / 2, size: size,
            creationIndex: ob.originIndex, creationTime: ob.startTime,
            invalidationIndex: ob.invalidatedIndex, invalidationTime: ob.invalidatedAt,
            confirmationIndex: ob.invalidatedIndex, confirmationTime: ob.invalidatedAt,
            sourceStructureEvent: ob.triggerEvent,
            status: 'active', fillPercentage: 0,
            mitigatedAt: null, mitigatedIndex: null, mitigationPrice: null,
            reInvalidatedAt: null, reInvalidatedIndex: null
          };

          for (var j = ob.invalidatedIndex + 1; j < n; j++) {
            if (bullishBreaker && C(j) < bottom) {
              breaker.status = 'invalidated';
              breaker.reInvalidatedAt = candles[j].time; breaker.reInvalidatedIndex = j;
              break;
            }
            if (!bullishBreaker && C(j) > top) {
              breaker.status = 'invalidated';
              breaker.reInvalidatedAt = candles[j].time; breaker.reInvalidatedIndex = j;
              break;
            }

            var extreme = bullishBreaker ? Lo(j) : H(j);
            var filled = bullishBreaker ? (top - extreme) : (extreme - bottom);
            if (filled < 0) filled = 0;
            if (filled > size) filled = size;
            var pct = size > 0 ? filled / size : 0;
            if (pct > breaker.fillPercentage) breaker.fillPercentage = pct;

            if (breaker.fillPercentage > 0 && breaker.status === 'active') {
              breaker.status = touchMode ? 'fully_mitigated' : 'partially_mitigated';
              if (touchMode) { breaker.mitigatedAt = candles[j].time; breaker.mitigatedIndex = j; breaker.mitigationPrice = extreme; }
            }
            if (!touchMode && breaker.fillPercentage >= 1) {
              breaker.status = 'fully_mitigated';
              breaker.mitigatedAt = candles[j].time; breaker.mitigatedIndex = j; breaker.mitigationPrice = extreme;
            }
            if (breaker.status === 'fully_mitigated') break;
          }

          breakers.push(breaker);
        } else if (ob.status === 'fully_mitigated') {
          mitigations.push({
            id: ob.id + '-mitigation',
            sourceOrderBlockId: ob.id,
            direction: ob.direction === 'bullish' ? 'BULLISH_MITIGATION' : 'BEARISH_MITIGATION',
            top: ob.top, bottom: ob.bottom, midpoint: ob.midpoint, size: ob.size,
            sourceIndex: ob.originIndex, sourceTime: ob.startTime,
            mitigationIndex: ob.mitigatedIndex, mitigationTime: ob.mitigatedAt,
            sourceStructureEvent: ob.triggerEvent,
            status: 'confirmed', active: true
          });
        }
      });

      breakers.sort(function (a, b) { return a.creationIndex - b.creationIndex; });
      mitigations.sort(function (a, b) { return a.sourceIndex - b.sourceIndex; });
      if (breakers.length > maxB) breakers = breakers.slice(-maxB);
      if (mitigations.length > maxB) mitigations = mitigations.slice(-maxB);
      return { breakers: breakers, mitigations: mitigations };
    },

    /**
     * Donchian Channels — current-candle-inclusive rolling window (matches
     * this engine's existing rolling-window convention, e.g. Calc.sma):
     * Upper[i] = max(high[i-length+1 .. i]), Lower[i] = min(low[i-length+1 .. i]).
     */
    donchian: function (candles, length) {
      var n = candles ? candles.length : 0;
      var upper = new Array(n).fill(null);
      var lower = new Array(n).fill(null);
      var middle = new Array(n).fill(null);

      var len = parseInt(length, 10);
      if (!candles || n === 0 || isNaN(len) || len < 1) {
        return { upper: upper, lower: lower, middle: middle };
      }

      for (var i = len - 1; i < n; i++) {
        var hh = -Infinity, ll = Infinity;
        for (var j = i - len + 1; j <= i; j++) {
          var h = Number(candles[j].high);
          var l = Number(candles[j].low);
          var c = Number(candles[j].close);
          if (!isFinite(h)) h = isFinite(c) ? c : hh;
          if (!isFinite(l)) l = isFinite(c) ? c : ll;
          if (h > hh) hh = h;
          if (l < ll) ll = l;
        }
        upper[i] = hh;
        lower[i] = ll;
        middle[i] = (hh + ll) / 2;
      }

      return { upper: upper, lower: lower, middle: middle };
    },

    /**
     * Stochastic Oscillator — %K, %D with Wilder/SMA smoothing
     * K Period (default 14): Lookback for Highest High & Lowest Low
     * K Smoothing (default 3): SMA smoothing of Raw %K
     * D Period (default 3): SMA smoothing of Smoothed %K
     * Returns: { k: Array, d: Array, rawK: Array }
     */
    stochastic: function (candles, kPeriod, kSmooth, dPeriod) {
      var n = candles ? candles.length : 0;
      var emptyArr = function () { return new Array(n).fill(null); };
      var kp = parseInt(kPeriod, 10);
      var ks = parseInt(kSmooth, 10);
      var dp = parseInt(dPeriod, 10);

      if (!candles || n === 0 || isNaN(kp) || kp < 1 || isNaN(ks) || ks < 1 || isNaN(dp) || dp < 1) {
        return { k: emptyArr(), d: emptyArr(), rawK: emptyArr() };
      }

      var rawK = emptyArr();
      var k = emptyArr();
      var d = emptyArr();

      // 1. Calculate Raw %K
      if (n >= kp) {
        for (var i = kp - 1; i < n; i++) {
          var hh = -Infinity;
          var ll = Infinity;
          for (var j = i - kp + 1; j <= i; j++) {
            var c = candles[j];
            var h = Number(c.high);
            var l = Number(c.low);
            var cl = Number(c.close);
            if (!isFinite(h)) h = isFinite(cl) ? cl : 0;
            if (!isFinite(l)) l = isFinite(cl) ? cl : 0;
            if (h > hh) hh = h;
            if (l < ll) ll = l;
          }

          var curClose = Number(candles[i].close);
          if (!isFinite(curClose)) curClose = (hh + ll) / 2.0;

          var range = hh - ll;
          var val;
          if (range === 0 || !isFinite(range)) {
            val = 50.0; // Deterministic zero-range handling
          } else {
            val = (100.0 * (curClose - ll)) / range;
          }
          // Clamp numerical floating point deviations to [0, 100]
          if (val < 0) val = 0;
          if (val > 100) val = 100;
          rawK[i] = val;
        }
      }

      // 2. Calculate Smoothed %K = SMA(Raw %K, kSmooth)
      var kMinIdx = kp - 1 + ks - 1;
      if (n > kMinIdx) {
        if (ks === 1) {
          for (var i = kp - 1; i < n; i++) {
            k[i] = rawK[i];
          }
        } else {
          var sumK = 0;
          for (var j = kp - 1; j <= kMinIdx; j++) {
            sumK += rawK[j];
          }
          k[kMinIdx] = Math.max(0, Math.min(100, sumK / ks));

          for (var i = kMinIdx + 1; i < n; i++) {
            sumK += rawK[i] - rawK[i - ks];
            k[i] = Math.max(0, Math.min(100, sumK / ks));
          }
        }
      }

      // 3. Calculate %D = SMA(Smoothed %K, dPeriod)
      var dMinIdx = kMinIdx + dp - 1;
      if (n > dMinIdx) {
        if (dp === 1) {
          for (var i = kMinIdx; i < n; i++) {
            d[i] = k[i];
          }
        } else {
          var sumD = 0;
          for (var j = kMinIdx; j <= dMinIdx; j++) {
            sumD += k[j];
          }
          d[dMinIdx] = Math.max(0, Math.min(100, sumD / dp));

          for (var i = dMinIdx + 1; i < n; i++) {
            sumD += k[i] - k[i - dp];
            d[i] = Math.max(0, Math.min(100, sumD / dp));
          }
        }
      }

      return { k: k, d: d, rawK: rawK };
    },

    /**
     * Average Directional Index (ADX) and Directional Movement Index (DMI)
     * Wilder's standard methodology.
     * Returns: { adx: Array, plusDi: Array, minusDi: Array, dx: Array }
     */
    adx: function (candles, diLength, adxSmoothing) {
      var n = candles ? candles.length : 0;
      var nullArr = function () { return new Array(n).fill(null); };
      var diL = parseInt(diLength, 10) || 14;
      var adxS = parseInt(adxSmoothing, 10) || diL;
      if (!candles || n === 0 || isNaN(diL) || diL < 1 || isNaN(adxS) || adxS < 1) {
        return { adx: nullArr(), plusDi: nullArr(), minusDi: nullArr(), dx: nullArr() };
      }

      var tr = new Array(n);
      var plusDm = new Array(n);
      var minusDm = new Array(n);

      for (var i = 0; i < n; i++) {
        var c = candles[i];
        var h = Number(c.high);
        var l = Number(c.low);
        var cl = Number(c.close);
        if (!isFinite(h)) h = cl;
        if (!isFinite(l)) l = cl;
        if (!isFinite(cl)) cl = (h + l) / 2;

        if (i === 0) {
          tr[0] = Math.abs(h - l);
          plusDm[0] = 0;
          minusDm[0] = 0;
        } else {
          var prev = candles[i - 1];
          var prevH = Number(prev.high);
          var prevL = Number(prev.low);
          var prevClose = Number(prev.close);
          if (!isFinite(prevH)) prevH = prevClose;
          if (!isFinite(prevL)) prevL = prevClose;
          if (!isFinite(prevClose)) prevClose = cl;

          var hl = Math.abs(h - l);
          var hpc = Math.abs(h - prevClose);
          var lpc = Math.abs(l - prevClose);
          tr[i] = Math.max(hl, hpc, lpc);

          var upMove = h - prevH;
          var downMove = prevL - l;

          if (upMove > downMove && upMove > 0) {
            plusDm[i] = upMove;
          } else {
            plusDm[i] = 0;
          }

          if (downMove > upMove && downMove > 0) {
            minusDm[i] = downMove;
          } else {
            minusDm[i] = 0;
          }
        }
      }

      var plusDi = nullArr();
      var minusDi = nullArr();
      var dx = nullArr();
      var adx = nullArr();

      if (n < diL) {
        return { adx: adx, plusDi: plusDi, minusDi: minusDi, dx: dx };
      }

      // Initial Wilder sum for TR, +DM, -DM
      var smoothedTr = new Array(n);
      var smoothedPlusDm = new Array(n);
      var smoothedMinusDm = new Array(n);

      var sumTr = 0, sumPlusDm = 0, sumMinusDm = 0;
      for (var i = 0; i < diL; i++) {
        sumTr += tr[i];
        sumPlusDm += plusDm[i];
        sumMinusDm += minusDm[i];
      }
      smoothedTr[diL - 1] = sumTr;
      smoothedPlusDm[diL - 1] = sumPlusDm;
      smoothedMinusDm[diL - 1] = sumMinusDm;

      if (sumTr === 0) {
        plusDi[diL - 1] = 0;
        minusDi[diL - 1] = 0;
      } else {
        plusDi[diL - 1] = Math.max(0, Math.min(100, 100 * sumPlusDm / sumTr));
        minusDi[diL - 1] = Math.max(0, Math.min(100, 100 * sumMinusDm / sumTr));
      }

      var sDi0 = plusDi[diL - 1] + minusDi[diL - 1];
      var dDi0 = Math.abs(plusDi[diL - 1] - minusDi[diL - 1]);
      dx[diL - 1] = sDi0 === 0 ? 0 : Math.max(0, Math.min(100, 100 * dDi0 / sDi0));

      // Subsequent Wilder smoothing for +DI, -DI, DX
      for (var i = diL; i < n; i++) {
        smoothedTr[i] = smoothedTr[i - 1] - (smoothedTr[i - 1] / diL) + tr[i];
        smoothedPlusDm[i] = smoothedPlusDm[i - 1] - (smoothedPlusDm[i - 1] / diL) + plusDm[i];
        smoothedMinusDm[i] = smoothedMinusDm[i - 1] - (smoothedMinusDm[i - 1] / diL) + minusDm[i];

        if (smoothedTr[i] === 0) {
          plusDi[i] = 0;
          minusDi[i] = 0;
        } else {
          plusDi[i] = Math.max(0, Math.min(100, 100 * smoothedPlusDm[i] / smoothedTr[i]));
          minusDi[i] = Math.max(0, Math.min(100, 100 * smoothedMinusDm[i] / smoothedTr[i]));
        }

        var sDi = plusDi[i] + minusDi[i];
        var dDi = Math.abs(plusDi[i] - minusDi[i]);
        dx[i] = sDi === 0 ? 0 : Math.max(0, Math.min(100, 100 * dDi / sDi));
      }

      // ADX = Wilder-smoothed DX (warm-up requires adxS DX values => (diL - 1) + (adxS - 1) index)
      var adxSeedIdx = (diL - 1) + (adxS - 1);
      if (n > adxSeedIdx) {
        var sumDx = 0;
        for (var i = diL - 1; i <= adxSeedIdx; i++) {
          sumDx += dx[i];
        }
        adx[adxSeedIdx] = sumDx / adxS;
        for (var i = adxSeedIdx + 1; i < n; i++) {
          adx[i] = (adx[i - 1] * (adxS - 1) + dx[i]) / adxS;
        }
      }

      return { adx: adx, plusDi: plusDi, minusDi: minusDi, dx: dx };
    },

    /**
     * On-Balance Volume (OBV)
     * Pure cumulative volume indicator based on price direction relative to previous close.
     * OBV[0] = 0.
     * If close[i] > close[i-1]: OBV[i] = OBV[i-1] + volume[i]
     * If close[i] < close[i-1]: OBV[i] = OBV[i-1] - volume[i]
     * If close[i] == close[i-1]: OBV[i] = OBV[i-1]
     */
    obv: function (candles) {
      var n = candles ? candles.length : 0;
      var out = new Array(n);
      if (n === 0) return out;
      out[0] = 0;
      var curObv = 0;
      for (var i = 1; i < n; i++) {
        var prevC = Number(candles[i - 1].close);
        var curC = Number(candles[i].close);
        var vol = Number(candles[i].volume);
        if (!isFinite(vol) || isNaN(vol) || vol < 0) vol = 0;
        if (!isFinite(prevC) || !isFinite(curC)) {
          // Keep curObv unchanged
        } else if (curC > prevC) {
          curObv += vol;
        } else if (curC < prevC) {
          curObv -= vol;
        }
        out[i] = curObv;
      }
      return out;
    },

    /**
     * Commodity Channel Index (CCI)
     * TP = (High + Low + Close) / 3
     * SMA_TP = SMA(TP, period)
     * MeanDeviation = mean(|TP[j] - SMA_TP| for j in window)
     * CCI = (TP - SMA_TP) / (0.015 * MeanDeviation)
     * Warm-up: first period - 1 elements are null.
     */
    cci: function (candles, period) {
      period = Number(period) || 20;
      if (period < 1) period = 20;
      var n = candles ? candles.length : 0;
      var out = new Array(n);
      for (var i = 0; i < n; i++) out[i] = null;
      if (n < period) return out;

      var tp = new Array(n);
      for (var i = 0; i < n; i++) {
        var h = Number(candles[i].high);
        var l = Number(candles[i].low);
        var c = Number(candles[i].close);
        if (!isFinite(h)) h = c;
        if (!isFinite(l)) l = c;
        tp[i] = (h + l + c) / 3;
      }

      for (var i = period - 1; i < n; i++) {
        var sum = 0;
        for (var j = i - period + 1; j <= i; j++) {
          sum += tp[j];
        }
        var smaTp = sum / period;

        var sumDev = 0;
        for (var j = i - period + 1; j <= i; j++) {
          sumDev += Math.abs(tp[j] - smaTp);
        }
        var meanDev = sumDev / period;

        if (meanDev === 0 || !isFinite(meanDev)) {
          out[i] = null;
        } else {
          out[i] = (tp[i] - smaTp) / (0.015 * meanDev);
        }
      }
      return out;
    },

    /**
     * Williams %R
     * For period N:
     *   HighestHigh = max(High) over last N candles
     *   LowestLow   = min(Low)  over last N candles
     *   %R = ((HighestHigh - Close) / (HighestHigh - LowestLow)) × -100
     * Range: [-100, 0]
     * Warm-up: first period-1 values are null.
     * Zero-range (HH == LL): returns null safely.
     */
    williamsR: function (candles, period) {
      var p = parseInt(period, 10);
      var n = candles ? candles.length : 0;
      var out = new Array(n).fill(null);
      if (!candles || n === 0 || isNaN(p) || p < 1) return out;

      for (var i = p - 1; i < n; i++) {
        var hh = -Infinity;
        var ll = Infinity;
        for (var j = i - p + 1; j <= i; j++) {
          var c = candles[j];
          var h = Number(c.high);
          var l = Number(c.low);
          var cl = Number(c.close);
          if (!isFinite(h)) h = isFinite(cl) ? cl : 0;
          if (!isFinite(l)) l = isFinite(cl) ? cl : 0;
          if (h > hh) hh = h;
          if (l < ll) ll = l;
        }

        var curClose = Number(candles[i].close);
        if (!isFinite(curClose)) curClose = (hh + ll) / 2;

        var range = hh - ll;
        if (range === 0 || !isFinite(range)) {
          out[i] = null;
        } else {
          out[i] = ((hh - curClose) / range) * -100;
        }
      }
      return out;
    },

    /**
     * Money Flow Index (MFI)
     * TP[i] = (High[i] + Low[i] + Close[i]) / 3
     * RMF[i] = TP[i] * Volume[i]
     * If TP[i] > TP[i-1]: PositiveFlow[i] = RMF[i], NegativeFlow[i] = 0
     * If TP[i] < TP[i-1]: NegativeFlow[i] = RMF[i], PositiveFlow[i] = 0
     * If TP[i] == TP[i-1] (or i == 0, no previous TP): both flows are 0
     * MFI = 100 * PositiveMF / (PositiveMF + NegativeMF) over a period window
     *   (equivalent to 100 - 100/(1+MoneyRatio), but avoids dividing by a
     *   zero NegativeMF directly)
     * If PositiveMF + NegativeMF == 0 for the window: no directional flow
     *   exists, so MFI is undefined for that candle (null), matching the
     *   engine's existing convention for undefined oscillator values.
     * Warm-up: first (period - 1) elements are null.
     */
    moneyFlowIndex: function (candles, period) {
      var p = parseInt(period, 10);
      var n = candles ? candles.length : 0;
      var out = new Array(n).fill(null);
      if (!candles || n === 0 || isNaN(p) || p < 1) return out;

      var tp = new Array(n);
      var posFlow = new Array(n);
      var negFlow = new Array(n);
      for (var i = 0; i < n; i++) {
        var c = candles[i];
        var h = Number(c.high);
        var l = Number(c.low);
        var cl = Number(c.close);
        if (!isFinite(h)) h = isFinite(cl) ? cl : 0;
        if (!isFinite(l)) l = isFinite(cl) ? cl : 0;
        if (!isFinite(cl)) cl = (h + l) / 2;
        tp[i] = (h + l + cl) / 3;

        var vol = Number(c.volume);
        if (!isFinite(vol) || vol < 0) vol = 0;
        var rmf = tp[i] * vol;

        if (i === 0) {
          posFlow[i] = 0;
          negFlow[i] = 0;
        } else if (tp[i] > tp[i - 1]) {
          posFlow[i] = rmf;
          negFlow[i] = 0;
        } else if (tp[i] < tp[i - 1]) {
          posFlow[i] = 0;
          negFlow[i] = rmf;
        } else {
          posFlow[i] = 0;
          negFlow[i] = 0;
        }
      }

      for (var i = p - 1; i < n; i++) {
        var posSum = 0, negSum = 0;
        for (var j = i - p + 1; j <= i; j++) {
          posSum += posFlow[j];
          negSum += negFlow[j];
        }
        var total = posSum + negSum;
        if (total === 0 || !isFinite(total)) {
          out[i] = null;
        } else {
          out[i] = (100 * posSum) / total;
        }
      }
      return out;
    },

    /**
     * Rate of Change (ROC) — percentage change vs. `period` bars ago.
     * ROC[i] = ((src[i] - src[i-period]) / src[i-period]) * 100
     * If src[i-period] is 0 (or non-finite), ROC[i] is left null instead of
     * dividing by zero. Optional signalPeriod runs an SMA over the ROC line
     * itself for a smoothed signal line (off — all null — when signalPeriod
     * is 0/falsy); reuses Calc.smoothIgnoringNulls rather than redefining
     * "SMA over a null-prefixed series", the same helper CCI's smoothed
     * line already uses.
     */
    roc: function (src, period, signalPeriod) {
      var n = src ? src.length : 0;
      var result = new Array(n).fill(null);
      var p = parseInt(period, 10);
      if (!src || n === 0 || isNaN(p) || p < 1) {
        return { roc: result, signal: new Array(n).fill(null) };
      }

      for (var i = p; i < n; i++) {
        var base = src[i - p];
        var cur = src[i];
        if (!isFinite(base) || base === 0 || !isFinite(cur)) continue;
        result[i] = ((cur - base) / base) * 100;
      }

      var sp = parseInt(signalPeriod, 10);
      var signal = (!isNaN(sp) && sp > 0) ? Calc.smoothIgnoringNulls(result, sp) : new Array(n).fill(null);
      return { roc: result, signal: signal };
    },

    /**
     * Aroon Up / Down / Oscillator.
     * For index i (needs a full period+1-bar window [i-period, i]):
     *   AroonUp[i]   = ((period - barsSinceHighestHigh) / period) * 100
     *   AroonDown[i] = ((period - barsSinceLowestLow)   / period) * 100
     *   AroonOsc[i]  = AroonUp[i] - AroonDown[i]
     * barsSinceHighestHigh/Low is 0 when the CURRENT bar is the window's
     * extreme, up to `period` when the extreme is the OLDEST bar in the
     * window. Ties resolve to the MOST RECENT occurrence (standard
     * convention: if the same high repeats within the window, the later
     * bar wins), matched here by using >=/<= while scanning forward so a
     * later equal value always overwrites an earlier one.
     *
     * Deliberate deviation: the window's high/low is found with a plain
     * O(period) inner scan per bar rather than a monotonic-deque
     * O(1)-amortized structure. This is the exact same shape of problem as
     * Calc.donchian's own rolling high/low elsewhere in this file, and
     * makes the same choice for the same reason — correctness first; at
     * the candle counts this engine actually renders (hundreds, not
     * millions) the O(n*period) total is not a measurable cost, and
     * nothing else in this codebase has (or needs) a monotonic-deque
     * helper to justify introducing one here.
     */
    aroon: function (candles, period) {
      var n = candles ? candles.length : 0;
      var up = new Array(n).fill(null);
      var down = new Array(n).fill(null);
      var osc = new Array(n).fill(null);

      var p = parseInt(period, 10);
      if (!candles || n === 0 || isNaN(p) || p < 1) return { up: up, down: down, osc: osc };

      function H(i) { var v = Number(candles[i].high); return isFinite(v) ? v : Number(candles[i].close); }
      function L(i) { var v = Number(candles[i].low); return isFinite(v) ? v : Number(candles[i].close); }

      for (var i = p; i < n; i++) {
        var hiIdx = i - p, loIdx = i - p;
        var hiVal = H(hiIdx), loVal = L(loIdx);
        for (var j = i - p + 1; j <= i; j++) {
          var h = H(j), l = L(j);
          if (h >= hiVal) { hiVal = h; hiIdx = j; }
          if (l <= loVal) { loVal = l; loIdx = j; }
        }
        var barsSinceHigh = i - hiIdx;
        var barsSinceLow = i - loIdx;
        up[i] = ((p - barsSinceHigh) / p) * 100;
        down[i] = ((p - barsSinceLow) / p) * 100;
        osc[i] = up[i] - down[i];
      }

      return { up: up, down: down, osc: osc };
    },

    /**
     * Chaikin Money Flow (CMF).
     * Step 1 (per-bar): MFM[i] = ((C-L)-(H-C)) / (H-L). A zero (or
     *   non-finite) H-L range is forced to MFM[i]=0 instead of propagating
     *   a divide-by-zero into the rolling sum — one doji/halted bar must
     *   not poison every window it's a member of.
     * Step 2 (per-bar): MFV[i] = MFM[i] * Volume[i]
     * Step 3 (rolling): CMF[i] = sum(MFV, last period) / sum(Volume, last
     *   period), using a running sum (add the bar entering the window,
     *   subtract the one leaving it) — the same O(1)-per-bar technique
     *   Calc.sma already uses, not a full re-sum every bar. If
     *   sum(Volume, period) is 0 for a window (an illiquid zero-volume
     *   stretch), CMF[i] is left null rather than dividing by zero.
     * First valid index is period-1 (period bars of history required),
     * matching this file's existing rolling-window convention (Calc.sma,
     * Calc.donchian, Calc.atr's seed).
     */
    cmf: function (candles, period) {
      var n = candles ? candles.length : 0;
      var result = new Array(n).fill(null);
      var p = parseInt(period, 10);
      if (!candles || n === 0 || isNaN(p) || p < 1 || n < p) return result;

      var mfv = new Array(n);
      var vol = new Array(n);
      for (var i = 0; i < n; i++) {
        var c = candles[i];
        var h = Number(c.high), l = Number(c.low), cl = Number(c.close);
        if (!isFinite(h)) h = cl;
        if (!isFinite(l)) l = cl;
        if (!isFinite(cl)) cl = (h + l) / 2;
        var range = h - l;
        var mfm = (!isFinite(range) || range === 0) ? 0 : (((cl - l) - (h - cl)) / range);
        var v = (c.volume !== undefined && c.volume !== null) ? Number(c.volume) : 0;
        if (!isFinite(v) || v < 0) v = 0;
        mfv[i] = mfm * v;
        vol[i] = v;
      }

      var sumMfv = 0, sumVol = 0;
      for (var i = 0; i < p; i++) { sumMfv += mfv[i]; sumVol += vol[i]; }
      result[p - 1] = sumVol === 0 ? null : (sumMfv / sumVol);
      for (var i = p; i < n; i++) {
        sumMfv += mfv[i] - mfv[i - p];
        sumVol += vol[i] - vol[i - p];
        result[i] = sumVol === 0 ? null : (sumMfv / sumVol);
      }

      return result;
    },

    /**
     * Ichimoku Kinko Hyo — pure calculation, indexed by CALCULATION index
     * (not display index). Displacement/projection is handled separately
     * by ichimokuDisplay(), which maps these calculation-index arrays onto
     * actual chart times.
     *
     * Tenkan-sen[i]  = (HighestHigh(i, conversionPeriod) + LowestLow(i, conversionPeriod)) / 2
     * Kijun-sen[i]   = (HighestHigh(i, basePeriod)       + LowestLow(i, basePeriod))       / 2
     * SpanA[i]       = (Tenkan[i] + Kijun[i]) / 2               (displayed at i + displacement)
     * SpanB[i]       = (HighestHigh(i, spanBPeriod) + LowestLow(i, spanBPeriod)) / 2  (displayed at i + displacement)
     * Chikou[i]      = Close[i]                                  (displayed at i - displacement)
     *
     * Every value at calculation index i uses ONLY candles[0..i] — no
     * candle beyond i is ever read. This is the entire no-look-ahead
     * guarantee: displacement only changes WHERE a value is drawn, never
     * what data produced it.
     */
    ichimoku: function (candles, conversionPeriod, basePeriod, spanBPeriod, displacement) {
      var convP = parseInt(conversionPeriod, 10);
      var baseP = parseInt(basePeriod, 10);
      var spanBP = parseInt(spanBPeriod, 10);
      var disp = parseInt(displacement, 10);
      var n = candles ? candles.length : 0;

      var tenkan  = new Array(n).fill(null);
      var kijun   = new Array(n).fill(null);
      var spanA   = new Array(n).fill(null);
      var spanB   = new Array(n).fill(null);
      var chikou  = new Array(n).fill(null);

      var valid = candles && n > 0 &&
        !isNaN(convP)  && convP  >= 1 &&
        !isNaN(baseP)  && baseP  >= 1 &&
        !isNaN(spanBP) && spanBP >= 1 &&
        !isNaN(disp)   && disp   >= 1;

      if (!valid) {
        return { tenkan: tenkan, kijun: kijun, spanA: spanA, spanB: spanB, chikou: chikou,
                 conversionPeriod: convP, basePeriod: baseP, spanBPeriod: spanBP, displacement: disp };
      }

      function highestLowest(idx, period) {
        var hh = -Infinity, ll = Infinity;
        for (var j = idx - period + 1; j <= idx; j++) {
          var c = candles[j];
          var h = Number(c.high);
          var l = Number(c.low);
          if (!isFinite(h)) h = isFinite(Number(c.close)) ? Number(c.close) : 0;
          if (!isFinite(l)) l = isFinite(Number(c.close)) ? Number(c.close) : 0;
          if (h > hh) hh = h;
          if (l < ll) ll = l;
        }
        return { hh: hh, ll: ll };
      }

      for (var i = convP - 1; i < n; i++) {
        var r = highestLowest(i, convP);
        tenkan[i] = (r.hh + r.ll) / 2;
      }
      for (var i = baseP - 1; i < n; i++) {
        var r = highestLowest(i, baseP);
        kijun[i] = (r.hh + r.ll) / 2;
      }
      for (var i = 0; i < n; i++) {
        if (tenkan[i] !== null && kijun[i] !== null) {
          spanA[i] = (tenkan[i] + kijun[i]) / 2;
        }
      }
      for (var i = spanBP - 1; i < n; i++) {
        var r = highestLowest(i, spanBP);
        spanB[i] = (r.hh + r.ll) / 2;
      }
      for (var i = 0; i < n; i++) {
        var cl = Number(candles[i].close);
        chikou[i] = isFinite(cl) ? cl : null;
      }

      return { tenkan: tenkan, kijun: kijun, spanA: spanA, spanB: spanB, chikou: chikou,
               conversionPeriod: convP, basePeriod: baseP, spanBPeriod: spanBP, displacement: disp };
    },

    /**
     * LWC candle.time comes in three shapes depending on timeframe:
     *   - number: Unix epoch seconds (intraday bars)
     *   - string: 'yyyy-mm-dd' (daily+ business-day bars)
     *   - object: { year, month, day } (LWC BusinessDay)
     * Arithmetic like `time - time` or `time + n` only works for numbers —
     * doing it directly on a string silently produces string concatenation
     * ("2026-09-02" + 60 === "2026-09-0260"), which is exactly the bug that
     * broke Ichimoku's forward projection on daily charts. These two
     * helpers convert any of the three shapes to/from epoch seconds so all
     * interval/projection math happens on numbers, then converts back to
     * whatever shape the input candles actually use.
     */
    _ichimokuTimeToEpoch: function (t) {
      if (typeof t === 'number') return t;
      if (typeof t === 'string') {
        var parts = t.split('-');
        if (parts.length === 3) {
          return Date.UTC(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10)) / 1000;
        }
        return NaN;
      }
      if (t && typeof t === 'object' && t.year !== undefined) {
        return Date.UTC(t.year, t.month - 1, t.day) / 1000;
      }
      return NaN;
    },

    _ichimokuEpochToTimeLike: function (epochSeconds, sampleTime) {
      if (typeof sampleTime === 'number') return epochSeconds;
      var d = new Date(epochSeconds * 1000);
      if (typeof sampleTime === 'string') {
        var y = d.getUTCFullYear();
        var m = String(d.getUTCMonth() + 1).padStart(2, '0');
        var day = String(d.getUTCDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
      }
      if (sampleTime && typeof sampleTime === 'object' && sampleTime.year !== undefined) {
        return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
      }
      return epochSeconds;
    },

    /**
     * Estimates the "typical" bar interval (in epoch SECONDS, regardless of
     * candle.time's own format) from the most recent candles, using the
     * MODE of successive deltas (not the mean/last-delta) so a single
     * weekend/holiday gap doesn't skew the projection used for future
     * whitespace bars.
     */
    _ichimokuBarInterval: function (candles) {
      var n = candles ? candles.length : 0;
      if (n < 2) return 86400;
      var start = Math.max(1, n - 20);
      var counts = {};
      var bestDelta = null, bestCount = 0;
      for (var i = start; i < n; i++) {
        var d = Calc._ichimokuTimeToEpoch(candles[i].time) - Calc._ichimokuTimeToEpoch(candles[i - 1].time);
        if (!(d > 0)) continue;
        counts[d] = (counts[d] || 0) + 1;
        if (counts[d] > bestCount) { bestCount = counts[d]; bestDelta = d; }
      }
      return bestDelta || 86400;
    },

    /**
     * Maps ichimoku()'s calculation-index arrays onto actual (or, for
     * Span A/B's forward projection beyond the last candle, synthetic)
     * chart times — this is the calculation-index vs display-index split
     * the spec requires. Each returned point retains calcIndex/calcTime
     * alongside its displayed time/value so a consumer can never confuse
     * "when this is drawn" with "what data produced it".
     */
    ichimokuDisplay: function (candles, calc) {
      var n = candles ? candles.length : 0;
      var disp = calc.displacement;
      var tenkanPts = [], kijunPts = [], chikouPts = [], spanAPts = [], spanBPts = [], cloudPts = [];
      if (n === 0 || !disp || disp < 1) {
        return { tenkan: tenkanPts, kijun: kijunPts, chikou: chikouPts, spanA: spanAPts, spanB: spanBPts, cloud: cloudPts };
      }

      var interval = Calc._ichimokuBarInterval(candles);
      var lastTime = candles[n - 1].time;
      var lastEpoch = Calc._ichimokuTimeToEpoch(lastTime);

      function displayTimeFor(displayIdx) {
        if (displayIdx < n) return candles[displayIdx].time;
        var futureEpoch = lastEpoch + (displayIdx - (n - 1)) * interval;
        return Calc._ichimokuEpochToTimeLike(futureEpoch, lastTime);
      }

      for (var i = 0; i < n; i++) {
        if (calc.tenkan[i] !== null) tenkanPts.push({ time: candles[i].time, value: calc.tenkan[i] });
        if (calc.kijun[i] !== null)  kijunPts.push({ time: candles[i].time, value: calc.kijun[i] });

        var chikouDisplayIdx = i - disp;
        if (chikouDisplayIdx >= 0 && calc.chikou[i] !== null) {
          chikouPts.push({ time: candles[chikouDisplayIdx].time, value: calc.chikou[i], calcIndex: i, calcTime: candles[i].time });
        }

        var forwardDisplayIdx = i + disp;
        var displayTime = displayTimeFor(forwardDisplayIdx);
        if (calc.spanA[i] !== null) spanAPts.push({ time: displayTime, value: calc.spanA[i], calcIndex: i, calcTime: candles[i].time });
        if (calc.spanB[i] !== null) spanBPts.push({ time: displayTime, value: calc.spanB[i], calcIndex: i, calcTime: candles[i].time });
        // Cloud only exists where BOTH spans are valid — no partial/fake fill.
        if (calc.spanA[i] !== null && calc.spanB[i] !== null) {
          cloudPts.push({ time: displayTime, spanA: calc.spanA[i], spanB: calc.spanB[i], calcIndex: i, calcTime: candles[i].time });
        }
      }

      return { tenkan: tenkanPts, kijun: kijunPts, chikou: chikouPts, spanA: spanAPts, spanB: spanBPts, cloud: cloudPts };
    }
  };

  // ═══════════════════════════════════════════════════
  // INDICATOR DEFINITIONS
  // ═══════════════════════════════════════════════════

  var DEFS = {
    SMA: {
      id: 'SMA', name: 'SMA', fullName: 'Simple Moving Average', type: 'overlay',
      paramDefs: {
        length: { label: 'Length',    type: 'int',    default: 20,    min: 1,   max: 500 },
        source: { label: 'Source',    type: 'source', default: 'close' },
        offset: { label: 'Offset',    type: 'int',    default: 0,     min: -500, max: 500 }
      },
      defaultColor: '#2962FF'
    },
    EMA: {
      id: 'EMA', name: 'EMA', fullName: 'Exponential Moving Average', type: 'overlay',
      paramDefs: {
        length: { label: 'Length',    type: 'int',    default: 20,    min: 1,   max: 500 },
        source: { label: 'Source',    type: 'source', default: 'close' },
        offset: { label: 'Offset',    type: 'int',    default: 0,     min: -500, max: 500 }
      },
      defaultColor: '#F2994A'
    },
    SUPERTREND: {
      id: 'SUPERTREND', name: 'Supertrend', fullName: 'Supertrend', type: 'overlay',
      paramDefs: {
        length:     { label: 'ATR Period',  type: 'int',   default: 10,  min: 1,   max: 500 },
        multiplier: { label: 'Multiplier',  type: 'float', default: 3.0, min: 0.1, max: 50 }
      },
      defaultColor: '#4FAF7B'
    },
    PSAR: {
      id: 'PSAR', name: 'PSAR', fullName: 'Parabolic SAR', type: 'overlay',
      paramDefs: {
        initialAF: { label: 'Initial AF',  type: 'float', default: 0.02, min: 0.001, max: 1.0 },
        increment: { label: 'AF Increment', type: 'float', default: 0.02, min: 0.001, max: 1.0 },
        maximumAF: { label: 'Maximum AF',  type: 'float', default: 0.20, min: 0.001, max: 1.0 }
      },
      defaultColor: '#4FAF7B'
    },
    PIVOTPOINTS: {
      id: 'PIVOTPOINTS', name: 'Pivot Points', fullName: 'Pivot Points', type: 'overlay',
      paramDefs: {
        method: { label: 'Method', type: 'select', default: 'Standard', options: ['Standard', 'Fibonacci', 'Woodie', 'Camarilla'] },
        period: { label: 'Period', type: 'select', default: 'Auto',     options: ['Auto', 'Daily', 'Weekly', 'Monthly'] }
      },
      defaultColor: '#FF9800'
    },
    PIVOTHIGHLOW: {
      id: 'PIVOTHIGHLOW', name: 'Pivot Points HL', fullName: 'Pivot Points High Low', type: 'overlay',
      paramDefs: {
        leftBars:  { label: 'Left Bars',  type: 'int', default: 5, min: 1, max: 500 },
        rightBars: { label: 'Right Bars', type: 'int', default: 5, min: 1, max: 500 }
      },
      defaultColor: '#EF5350'
    },
    MARKETSTRUCTURE: {
      // ONE user-facing indicator — BOS/CHoCH/MSS are independent toggles
      // inside it (paramDefs below), not three separate registry entries.
      // Internally Calc.marketStructure still treats them as distinct
      // event types; these three params only filter which of that single
      // calculation's events get drawn.
      id: 'MARKETSTRUCTURE', name: 'Market Structure', fullName: 'Market Structure', type: 'overlay',
      paramDefs: {
        swingLength:           { label: 'Swing Length',       type: 'int',    default: 5,   min: 1,   max: 100 },
        confirmation:          { label: 'Break Confirmation', type: 'select', default: 'Close', options: ['Close', 'Wick'] },
        showBOS:               { label: 'BOS',                type: 'select', default: 'On', options: ['On', 'Off'] },
        showCHoCH:             { label: 'CHoCH',               type: 'select', default: 'On', options: ['On', 'Off'] },
        showMSS:               { label: 'MSS',                 type: 'select', default: 'On', options: ['On', 'Off'] },
        showSwingLabels:       { label: 'Show Swing Labels',   type: 'select', default: 'On', options: ['On', 'Off'] },
        displacementMultiplier:{ label: 'MSS Displacement (x ATR)', type: 'float', default: 1.5, min: 0.1, max: 10 },
        atrLength:             { label: 'MSS ATR Length',      type: 'int',    default: 14,  min: 1,   max: 500 }
      },
      defaultColor: '#4FAF7B'
    },
    FVG: {
      // ONE user-facing indicator — bullish/bearish/mitigated/extend are
      // independent toggles inside it (paramDefs below), matching Market
      // Structure's own BOS/CHoCH/MSS pattern rather than three registry
      // entries. Calc.fvg always computes every gap; these params only
      // filter which of that single calculation's zones get drawn.
      id: 'FVG', name: 'Fair Value Gap', fullName: 'Fair Value Gap', type: 'overlay',
      paramDefs: {
        showBullish:  { label: 'Bullish FVG',   type: 'select', default: 'On', options: ['On', 'Off'] },
        showBearish:  { label: 'Bearish FVG',   type: 'select', default: 'On', options: ['On', 'Off'] },
        mitigation:   { label: 'Mitigation',    type: 'select', default: 'Full Fill', options: ['Touch', 'Full Fill'] },
        showMitigated:{ label: 'Show Mitigated',type: 'select', default: 'On', options: ['On', 'Off'] },
        extendZones:  { label: 'Extend Zones',  type: 'select', default: 'On', options: ['On', 'Off'] },
        extendBars:   { label: 'Extend Length (bars)', type: 'int', default: 25, min: 1, max: 500 },
        showMidpoint: { label: 'Show 50% Level',type: 'select', default: 'Off', options: ['On', 'Off'] },
        minSizeATRMultiplier: { label: 'Min Size (x ATR)', type: 'float', default: 0.1, min: 0, max: 10 },
        atrLength:    { label: 'ATR Length',    type: 'int',    default: 14, min: 1, max: 500 },
        // 500 (this setting's own ceiling), not 50 — a low default silently
        // dropped the OLDEST zones once a symbol's full loaded history
        // (especially intraday, with many small qualifying gaps) exceeded
        // it, which showed up as "no FVGs when I scroll left into older
        // candles" even though those candles genuinely had them.
        maxZones:     { label: 'Max Zones Shown', type: 'int',  default: 500, min: 1, max: 500 }
      },
      defaultColor: '#4FAF7B'
    },
    OB: {
      // ONE user-facing indicator — bullish/bearish/mitigated/extend are
      // independent toggles (paramDefs below), same pattern as Market
      // Structure/FVG. Calc.orderBlocks always computes every block; these
      // params only filter which of that single calculation's zones draw.
      // No displacement/ATR filter — the reference TradingView script this
      // was ported from (see Calc.orderBlocks docstring) has none; its
      // rolling-low crossunder trigger is the only qualification needed.
      id: 'OB', name: 'Order Blocks', fullName: 'Order Blocks', type: 'overlay',
      paramDefs: {
        swingLength:  { label: 'Swing Length',   type: 'int',    default: 5,   min: 1,   max: 100 },
        showBullish:  { label: 'Bullish OB',     type: 'select', default: 'On', options: ['On', 'Off'] },
        showBearish:  { label: 'Bearish OB',     type: 'select', default: 'On', options: ['On', 'Off'] },
        mitigation:   { label: 'Mitigation',     type: 'select', default: 'Full Fill', options: ['Touch', 'Full Fill'] },
        showMitigated:{ label: 'Show Mitigated', type: 'select', default: 'Off', options: ['On', 'Off'] },
        extendZones:  { label: 'Extend Zones',   type: 'select', default: 'On', options: ['On', 'Off'] },
        showMidpoint: { label: 'Show 50% Level', type: 'select', default: 'Off', options: ['On', 'Off'] },
        maxZones:     { label: 'Max Zones Shown', type: 'int',   default: 50, min: 1, max: 500 }
      },
      defaultColor: '#3A311C'
    },
    LIQUIDITY: {
      // ONE user-facing indicator — EQH/EQL/swing-liquidity/labels/extend
      // are independent toggles (paramDefs below). Calc.liquidity always
      // computes every pool; these params only filter which get drawn.
      id: 'LIQUIDITY', name: 'Liquidity (EQH/EQL)', fullName: 'Liquidity (EQH/EQL)', type: 'overlay',
      paramDefs: {
        swingLength:            { label: 'Swing Length',            type: 'int',    default: 5,    min: 1, max: 100 },
        showEqualHighs:         { label: 'Equal Highs',              type: 'select', default: 'On', options: ['On', 'Off'] },
        showEqualLows:          { label: 'Equal Lows',               type: 'select', default: 'On', options: ['On', 'Off'] },
        showSwingHighLiquidity: { label: 'Swing High Liquidity',     type: 'select', default: 'Off', options: ['On', 'Off'] },
        showSwingLowLiquidity:  { label: 'Swing Low Liquidity',      type: 'select', default: 'Off', options: ['On', 'Off'] },
        minTouches:             { label: 'Minimum Touches',          type: 'int',    default: 2,    min: 2, max: 10 },
        toleranceATRMultiplier: { label: 'Tolerance (x ATR)',        type: 'float',  default: 0.05, min: 0, max: 5 },
        atrLength:              { label: 'ATR Length',               type: 'int',    default: 14,   min: 1, max: 500 },
        extendLevels:           { label: 'Extend Levels',            type: 'select', default: 'On', options: ['On', 'Off'] },
        showLabels:             { label: 'Show Labels',              type: 'select', default: 'On', options: ['On', 'Off'] },
        showSweptLevels:        { label: 'Show Swept Levels',        type: 'select', default: 'On', options: ['On', 'Off'] },
        maxPools:               { label: 'Max Pools Shown',          type: 'int',    default: 50,   min: 1, max: 500 }
      },
      defaultColor: '#2962FF'
    },
    LIQUIDITYSWEEPS: {
      // ONE user-facing indicator — buy/sell-side/labels/swept are
      // independent toggles (paramDefs below). Internally BUY_SIDE_SWEEP
      // and SELL_SIDE_SWEEP remain distinct event types (see
      // Calc.liquiditySweeps). Reuses Calc.liquidity's own pool params
      // (swingLength/tolerance/minTouches) since it must call that exact
      // function to obtain pools — never rediscovering EQH/EQL itself.
      id: 'LIQUIDITYSWEEPS', name: 'Liquidity Sweeps', fullName: 'Liquidity Sweeps', type: 'overlay',
      paramDefs: {
        swingLength:            { label: 'Swing Length',        type: 'int',    default: 5,    min: 1, max: 100 },
        toleranceATRMultiplier: { label: 'Pool Tolerance (x ATR)', type: 'float', default: 0.05, min: 0, max: 2 },
        atrLength:              { label: 'ATR Length',          type: 'int',    default: 14,   min: 1, max: 500 },
        minTouches:             { label: 'Minimum Touches',     type: 'int',    default: 2,    min: 2, max: 10 },
        showBuySide:            { label: 'Buy-Side Sweeps',     type: 'select', default: 'On', options: ['On', 'Off'] },
        showSellSide:           { label: 'Sell-Side Sweeps',    type: 'select', default: 'On', options: ['On', 'Off'] },
        confirmation:           { label: 'Confirmation',        type: 'select', default: 'Close Rejection', options: ['Close Rejection', 'Wick + Reclaim'] },
        showLabels:             { label: 'Show Sweep Labels',   type: 'select', default: 'On', options: ['On', 'Off'] },
        showSweptLevels:        { label: 'Show Swept Levels',   type: 'select', default: 'On', options: ['On', 'Off'] },
        maxEvents:              { label: 'Max Events Shown',    type: 'int',    default: 50,   min: 1, max: 500 }
      },
      defaultColor: '#EF5350'
    },
    PREMIUMDISCOUNT: {
      // ONE user-facing indicator — Premium/Equilibrium/Discount/Dealing
      // Range/Delta Volume are independent toggles inside it (paramDefs below), never
      // separate registry entries. Reuses Calc.marketStructure's swings
      // directly (via Calc.premiumDiscount) — no second swing detector.
      id: 'PREMIUMDISCOUNT', name: 'Premium & Discount Delta Volume', fullName: 'Premium & Discount Delta Volume', type: 'overlay',
      paramDefs: {
        swingLength:      { label: 'Swing Length',      type: 'int',    default: 5,    min: 1,  max: 100 },
        showPremium:      { label: 'Premium Zone',      type: 'select', default: 'On', options: ['On', 'Off'] },
        showDiscount:     { label: 'Discount Zone',     type: 'select', default: 'On', options: ['On', 'Off'] },
        showEquilibrium:  { label: 'Equilibrium (50%)', type: 'select', default: 'On', options: ['On', 'Off'] },
        showDealingRange: { label: 'Dealing Range',     type: 'select', default: 'On', options: ['On', 'Off'] },
        showDeltaVolume:  { label: 'Delta Volume %',    type: 'select', default: 'On', options: ['On', 'Off'] },
        showMacroRange:   { label: 'Macro Range Bands', type: 'select', default: 'On', options: ['On', 'Off'] },
        zoneOpacity:      { label: 'Zone Opacity',      type: 'float',  default: 0.08, min: 0.02, max: 0.3 }
      },
      defaultColor: '#787b86'
    },
    SMC: {
      // The combined "Smart Money Concepts" indicator — pure orchestration
      // over the six standalone engines above (Market Structure, FVG,
      // Order Blocks, Liquidity, Liquidity Sweeps, Premium & Discount).
      // Detects nothing new itself; see Calc.smcSetups for the exact
      // confluence rule and scoring.
      id: 'SMC', name: 'SMC Setups', fullName: 'Smart Money Concepts (SMC) Setups', type: 'overlay',
      paramDefs: {
        swingLength:            { label: 'Swing Length',            type: 'int',    default: 5,    min: 1, max: 100 },
        toleranceATRMultiplier: { label: 'Liquidity Tolerance (x ATR)', type: 'float', default: 0.05, min: 0, max: 2 },
        atrLength:              { label: 'ATR Length',               type: 'int',    default: 14,   min: 1, max: 500 },
        minTouches:             { label: 'Minimum Touches',          type: 'int',    default: 2,    min: 2, max: 10 },
        mitigation:             { label: 'FVG/OB Mitigation',        type: 'select', default: 'Full Fill', options: ['Touch', 'Full Fill'] },
        confirmation:           { label: 'Sweep Confirmation',       type: 'select', default: 'Close Rejection', options: ['Close Rejection', 'Wick + Reclaim'] },
        structureWindowBars:    { label: 'Structure Window (bars)',  type: 'int',    default: 10,   min: 1, max: 100 },
        minScore:               { label: 'Minimum Score',            type: 'int',    default: 2,    min: 2, max: 5 },
        showBullish:            { label: 'Bullish Setups',           type: 'select', default: 'On', options: ['On', 'Off'] },
        showBearish:            { label: 'Bearish Setups',           type: 'select', default: 'On', options: ['On', 'Off'] },
        showLabels:             { label: 'Show Setup Labels',        type: 'select', default: 'On', options: ['On', 'Off'] },
        maxSetups:              { label: 'Max Setups Shown',         type: 'int',    default: 20,   min: 1, max: 200 }
      },
      defaultColor: '#B388FF'
    },
    BREAKERMITIGATION: {
      // ONE user-facing indicator — Bullish/Bearish Breakers and Bullish/
      // Bearish Mitigation are independent toggles inside it (paramDefs
      // below), never separate registry entries. Reuses Calc.orderBlocks
      // directly (via Calc.breakerMitigation) — Order Block detection is
      // never recalculated or reimplemented here.
      id: 'BREAKERMITIGATION', name: 'Breaker & Mitigation Blocks', fullName: 'Breaker & Mitigation Blocks', type: 'overlay',
      paramDefs: {
        swingLength:            { label: 'Swing Length',           type: 'int',    default: 5,   min: 1,   max: 100 },
        breakerMitigation:      { label: 'Breaker Mitigation',      type: 'select', default: 'Full Fill', options: ['Touch', 'Full Fill'] },
        showBullishBreakers:    { label: 'Bullish Breakers',        type: 'select', default: 'On', options: ['On', 'Off'] },
        showBearishBreakers:    { label: 'Bearish Breakers',        type: 'select', default: 'On', options: ['On', 'Off'] },
        showBullishMitigation:  { label: 'Bullish Mitigation',      type: 'select', default: 'On', options: ['On', 'Off'] },
        showBearishMitigation:  { label: 'Bearish Mitigation',      type: 'select', default: 'On', options: ['On', 'Off'] },
        extendActive:           { label: 'Extend Active Blocks',    type: 'select', default: 'On', options: ['On', 'Off'] },
        showLabels:             { label: 'Show Labels',             type: 'select', default: 'On', options: ['On', 'Off'] },
        showHistorical:         { label: 'Show Historical Blocks',  type: 'select', default: 'On', options: ['On', 'Off'] },
        showMitigatedBreakers:  { label: 'Show Mitigated Breakers', type: 'select', default: 'On', options: ['On', 'Off'] },
        maxBlocks:              { label: 'Maximum Active Blocks',   type: 'int',    default: 50,  min: 1,   max: 500 }
      },
      defaultColor: '#FF9800'
    },
    DONCHIAN: {
      id: 'DONCHIAN', name: 'Donchian Channels', fullName: 'Donchian Channels', type: 'overlay',
      paramDefs: {
        length: { label: 'Length', type: 'int', default: 20, min: 1, max: 500 },
        offset: { label: 'Offset', type: 'int', default: 0, min: -500, max: 500 }
      },
      defaultColor: '#2962FF'
    },
    KELTNER: {
      id: 'KELTNER', name: 'Keltner Channels', fullName: 'Keltner Channels', type: 'overlay',
      paramDefs: {
        length:     { label: 'EMA Length', type: 'int',   default: 20,  min: 1,   max: 500 },
        atrLength:  { label: 'ATR Length', type: 'int',   default: 10,  min: 1,   max: 500 },
        multiplier: { label: 'Multiplier', type: 'float', default: 2.0, min: 0.1, max: 50 }
      },
      defaultColor: '#2962FF'
    },
    RSI: {
      id: 'RSI', name: 'RSI', fullName: 'RSI', type: 'pane',
      paramDefs: {
        length: { label: 'Length',     type: 'int',   default: 14,  min: 1,  max: 200 },
        source: { label: 'Source',     type: 'source',default: 'close' },
        upper:  { label: 'Overbought', type: 'float', default: 70,  min: 50, max: 100 },
        lower:  { label: 'Oversold',   type: 'float', default: 30,  min: 0,  max: 50 }
      },
      defaultColor: '#B388FF'
    },
    STOCH: {
      id: 'STOCH', name: 'Stochastic', fullName: 'Stochastic Oscillator', type: 'pane',
      paramDefs: {
        kPeriod: { label: '%K Period',    type: 'int', default: 14, min: 1, max: 200 },
        kSmooth: { label: '%K Smoothing', type: 'int', default: 3,  min: 1, max: 50 },
        dPeriod: { label: '%D Period',    type: 'int', default: 3,  min: 1, max: 50 }
      },
      defaultColor: '#2A75FF'
    },
    DMI: {
      id: 'DMI', name: 'DMI', fullName: 'Directional Movement Index (DMI)', type: 'pane',
      paramDefs: {
        diLength:     { label: 'DI Length',     type: 'int', default: 14, min: 1, max: 200 },
        adxSmoothing: { label: 'ADX Smoothing', type: 'int', default: 14, min: 1, max: 200 }
      },
      defaultColor: '#E91E63'
    },
    ADX: {
      id: 'ADX', name: 'ADX', fullName: 'Average Directional Index (ADX)', type: 'pane',
      paramDefs: {
        adxSmoothing: { label: 'ADX Smoothing', type: 'int', default: 14, min: 1, max: 200 },
        diLength:     { label: 'DI Length',     type: 'int', default: 14, min: 1, max: 200 }
      },
      defaultColor: '#E91E63'
    },
    MACD: {
      id: 'MACD', name: 'MACD', fullName: 'MACD', type: 'pane',
      paramDefs: {
        fast:   { label: 'Fast Length',   type: 'int',   default: 12, min: 1, max: 200 },
        slow:   { label: 'Slow Length',   type: 'int',   default: 26, min: 1, max: 200 },
        signal: { label: 'Signal Length', type: 'int',   default: 9,  min: 1, max: 200 },
        source: { label: 'Source',        type: 'source',default: 'close' }
      },
      defaultColor: '#2962FF'
    },
    BB: {
      id: 'BB', name: 'BB', fullName: 'Bollinger Bands', type: 'overlay',
      paramDefs: {
        length: { label: 'Length',     type: 'int',   default: 20,  min: 1,  max: 500 },
        source: { label: 'Source',     type: 'source',default: 'close' },
        stdDev: { label: 'StdDev Mult',type: 'float', default: 2.0, min: 0.1, max: 10 }
      },
      defaultColor: '#2962FF'
    },
    VWAP: {
      id: 'VWAP', name: 'VWAP', fullName: 'VWAP (Volume Weighted Average Price)', type: 'overlay',
      paramDefs: {
        source:   { label: 'Source',          type: 'source', default: 'hlc3' },
        anchor:   { label: 'Anchor',          type: 'select', default: 'Session', options: ['Session'] },
        bands:    { label: 'Bands',           type: 'select', default: 'On',      options: ['On', 'Off'] },
        bandMult: { label: 'Band Multiplier', type: 'float',  default: 1.0,       min: 0.1, max: 10 }
      },
      defaultColor: '#6B8FD6'
    },
    VOLUME: {
      id: 'VOLUME', name: 'Volume MA', fullName: 'Volume MA', type: 'volume',
      paramDefs: {
        maLength: { label: 'MA Length', type: 'int', default: 20, min: 1, max: 200 }
      },
      defaultColor: '#F2994A'
    },
    OBV: {
      id: 'OBV', name: 'OBV', fullName: 'On-Balance Volume', type: 'pane',
      paramDefs: {},
      defaultColor: '#2962FF'
    },
    CCI: {
      id: 'CCI', name: 'CCI', fullName: 'Commodity Channel Index', type: 'pane',
      paramDefs: {
        length:       { label: 'Length',           type: 'int', default: 20, min: 1, max: 500 },
        smoothLength: { label: 'Smoothing Length',  type: 'int', default: 14, min: 1, max: 500 }
      },
      defaultColor: '#2A75FF'
    },
    ATR: {
      id: 'ATR', name: 'ATR', fullName: 'Average True Range', type: 'pane',
      paramDefs: {
        length: { label: 'Length', type: 'int', default: 14, min: 1, max: 500 }
      },
      defaultColor: '#5C9CE6'
    },
    WILLIAMSR: {
      id: 'WILLIAMSR', name: 'Williams %R', fullName: 'Williams %R', type: 'pane',
      paramDefs: {
        length: { label: 'Period', type: 'int', default: 14, min: 1, max: 500 }
      },
      defaultColor: '#855EC9'
    },
    MFI: {
      id: 'MFI', name: 'MFI', fullName: 'Money Flow Index', type: 'pane',
      paramDefs: {
        length: { label: 'Period', type: 'int', default: 14, min: 1, max: 500 }
      },
      defaultColor: '#B388FF'
    },
    ROC: {
      id: 'ROC', name: 'ROC', fullName: 'Rate of Change', type: 'pane',
      paramDefs: {
        length:       { label: 'Length',        type: 'int',    default: 12, min: 1, max: 500 },
        source:       { label: 'Source',        type: 'source', default: 'close' },
        signalPeriod: { label: 'Signal Length', type: 'int',    default: 0,  min: 0, max: 200 }
      },
      defaultColor: '#2962FF'
    },
    AROON: {
      id: 'AROON', name: 'Aroon', fullName: 'Aroon', type: 'pane',
      paramDefs: {
        length: { label: 'Length', type: 'int', default: 14, min: 1, max: 500 }
      },
      defaultColor: '#FF9800'
    },
    CMF: {
      id: 'CMF', name: 'CMF', fullName: 'Chaikin Money Flow', type: 'pane',
      paramDefs: {
        length: { label: 'Length', type: 'int', default: 20, min: 1, max: 500 }
      },
      defaultColor: '#26A69A'
    },
    ICHIMOKU: {
      id: 'ICHIMOKU', name: 'Ichimoku', fullName: 'Ichimoku Cloud', type: 'overlay',
      paramDefs: {
        conversionPeriod: { label: 'Conversion Period', type: 'int', default: 9,  min: 1, max: 500 },
        basePeriod:       { label: 'Base Period',       type: 'int', default: 26, min: 1, max: 500 },
        spanBPeriod:      { label: 'Leading Span B Period', type: 'int', default: 52, min: 1, max: 500 },
        displacement:     { label: 'Displacement',      type: 'int', default: 26, min: 1, max: 500 }
      },
      defaultColor: '#EF9A9A'
    }
  };

  // Color palette for multiple EMA instances
  var EMA_COLORS = ['#F2994A', '#AB47BC', '#26A69A', '#EF5350', '#42A5F5', '#FFCA28', '#EC407A'];

  // ═══════════════════════════════════════════════════
  // ENGINE STATE
  // ═══════════════════════════════════════════════════

  var _instances  = {};   // instanceId → instance object
  var _nextId     = 1;
  var _candles    = [];   // current displayData candles [{time,open,high,low,close,volume}]
  var _range      = null;
  var _persistenceRestored = false;
  var _menuSearchText = '';

  function _newId() { return 'i' + (_nextId++); }

  // ═══════════════════════════════════════════════════
  // CONFIGURATION PERSISTENCE (localStorage)
  // ═══════════════════════════════════════════════════
  // Persists only CONFIGURATION (type/params/visibility/color), never
  // calculated values — those are always regenerated from live candle
  // data on load. Versioned so a future schema change can migrate/discard
  // old data instead of crashing on it.

  var PERSIST_KEY = 'leverage_indicators_v1';
  var PERSIST_VERSION = 1;

  /** Pure validation: parsed-JSON → clean instance-config array, or [] on any corruption. */
  function _sanitizeConfig(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
    if (raw.version !== PERSIST_VERSION) return [];
    if (!Array.isArray(raw.instances)) return [];
    var out = [];
    raw.instances.forEach(function (entry) {
      if (!entry || typeof entry !== 'object') return;
      var type = entry.type;
      if (typeof type !== 'string' || !DEFS[type]) return; // unknown/removed indicator -> skip this entry only
      var params = (entry.params && typeof entry.params === 'object' && !Array.isArray(entry.params)) ? entry.params : {};
      var visible = entry.visible !== false;
      var color = typeof entry.color === 'string' ? entry.color : undefined;
      out.push({ type: type, params: params, visible: visible, color: color });
    });
    return out;
  }

  function _serializeConfig() {
    var list = [];
    for (var id in _instances) {
      var inst = _instances[id];
      list.push({ type: inst.def.id, params: inst.params, visible: inst.visible, color: inst.color });
    }
    return { version: PERSIST_VERSION, instances: list };
  }

  function _savePersistedConfig() {
    try {
      if (!window.localStorage) return;
      window.localStorage.setItem(PERSIST_KEY, JSON.stringify(_serializeConfig()));
    } catch (e) { /* storage unavailable/full — persistence is best-effort */ }
  }

  function _loadPersistedConfig() {
    try {
      if (!window.localStorage) return [];
      var raw = window.localStorage.getItem(PERSIST_KEY);
      if (!raw) return [];
      var parsed;
      try { parsed = JSON.parse(raw); } catch (e) { return []; }
      return _sanitizeConfig(parsed);
    } catch (e) { return []; }
  }

  // ═══════════════════════════════════════════════════
  // PANE MANAGER — separate LWC charts for RSI / MACD
  // ═══════════════════════════════════════════════════

  var _isSyncingRange = false;
  function _broadcastLogicalRange(sourceChart, lr) {
    if (_isSyncingRange || !lr) return;
    _isSyncingRange = true;
    try {
      if (window.bigChart && window.bigChart !== sourceChart) {
        window.bigChart.timeScale().setVisibleLogicalRange(lr);
      }
      for (var pid in PaneManager._panes) {
        var p = PaneManager._panes[pid];
        if (p && p.chart && p.chart !== sourceChart) {
          p.chart.timeScale().setVisibleLogicalRange(lr);
        }
      }
    } catch (e) {}
    _isSyncingRange = false;
    _drawBbCloud();
  }

  var _bbCanvas = null;
  function _drawOverlayClouds() {
    _drawPaneClouds();

    // Pivot Points High Low markers — LWC's setMarkers positions
    // aboveBar/belowBar markers relative to the series' OWN bar data, so
    // they must live on the real candle series (empty dummy series produce
    // no visible marker at all). setMarkers REPLACES a series' full marker
    // list each call, so every visible instance's markers are merged here
    // into one sorted array and set ONCE — independent of the canvas-based
    // rendering below, so markers still get cleared correctly even when a
    // PivotHighLow instance is removed and nothing else is active.
    if (window.bigCandleSeries) {
      var allPivotMarkers = [];
      for (var pid in _instances) {
        var pinst = _instances[pid];
        if (pinst.def.id === 'PIVOTHIGHLOW' && pinst.visible && pinst._pivotMarkers) {
          allPivotMarkers = allPivotMarkers.concat(pinst._pivotMarkers);
        }
        // Market Structure's HH/HL/LH/LL swing labels merge into the same
        // setMarkers() call, same reasoning as Pivot Points High Low above
        // — LWC's setMarkers REPLACES the whole list per call, so every
        // marker-producing indicator must contribute to one combined array.
        if (pinst.def.id === 'MARKETSTRUCTURE' && pinst.visible && pinst._msMarkers) {
          allPivotMarkers = allPivotMarkers.concat(pinst._msMarkers);
        }
      }
      allPivotMarkers.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
      try { window.bigCandleSeries.setMarkers(allPivotMarkers); } catch (e) {}
    }

    var bbInst = null;
    var vwapInst = null;
    var stInst = null;
    var ichimokuInsts = [];
    var pivotInsts = [];
    var donchianInsts = [];
    var keltnerInsts = [];
    var marketStructureInsts = [];
    var fvgInsts = [];
    var obInsts = [];
    var liquidityInsts = [];
    var liquiditySweepInsts = [];
    var premiumDiscountInsts = [];
    var smcInsts = [];
    var breakerMitigationInsts = [];
    for (var id in _instances) {
      if (_instances[id].def.id === 'BB' && _instances[id].visible) {
        bbInst = _instances[id];
      }
      if (_instances[id].def.id === 'VWAP' && _instances[id].visible && _instances[id].params.bands === 'On') {
        vwapInst = _instances[id];
      }
      if (_instances[id].def.id === 'SUPERTREND' && _instances[id].visible) {
        stInst = _instances[id];
      }
      if (_instances[id].def.id === 'ICHIMOKU' && _instances[id].visible) {
        ichimokuInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'PIVOTPOINTS' && _instances[id].visible) {
        pivotInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'DONCHIAN' && _instances[id].visible) {
        donchianInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'KELTNER' && _instances[id].visible) {
        keltnerInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'MARKETSTRUCTURE' && _instances[id].visible) {
        marketStructureInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'FVG' && _instances[id].visible) {
        fvgInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'OB' && _instances[id].visible) {
        obInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'LIQUIDITY' && _instances[id].visible) {
        liquidityInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'LIQUIDITYSWEEPS' && _instances[id].visible) {
        liquiditySweepInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'PREMIUMDISCOUNT' && _instances[id].visible) {
        premiumDiscountInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'SMC' && _instances[id].visible) {
        smcInsts.push(_instances[id]);
      }
      if (_instances[id].def.id === 'BREAKERMITIGATION' && _instances[id].visible) {
        breakerMitigationInsts.push(_instances[id]);
      }
    }
    var chartContainer = document.getElementById('chart-container');
    if ((!bbInst && !vwapInst && !stInst && ichimokuInsts.length === 0 && pivotInsts.length === 0 && donchianInsts.length === 0 && keltnerInsts.length === 0 && marketStructureInsts.length === 0 && fvgInsts.length === 0 && obInsts.length === 0 && liquidityInsts.length === 0 && liquiditySweepInsts.length === 0 && premiumDiscountInsts.length === 0 && smcInsts.length === 0 && breakerMitigationInsts.length === 0) || !window.bigChart || !chartContainer || !_candles || !_candles.length) {
      if (_bbCanvas && _bbCanvas.parentNode) _bbCanvas.parentNode.removeChild(_bbCanvas);
      _bbCanvas = null;
      return;
    }

    if (!_bbCanvas || !_bbCanvas.parentNode) {
      _bbCanvas = document.createElement('canvas');
      _bbCanvas.id = 'lwc-bb-cloud-canvas';
      _bbCanvas.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
      var firstChild = chartContainer.querySelector('#chart') || chartContainer.firstChild;
      if (firstChild && firstChild.parentNode === chartContainer) {
        chartContainer.insertBefore(_bbCanvas, firstChild);
      } else {
        chartContainer.insertBefore(_bbCanvas, chartContainer.firstChild);
      }
    } else {
      _bbCanvas.style.zIndex = '0';
    }

    var rect = chartContainer.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.round(rect.width);
    var h = Math.round(rect.height);
    if (w <= 0 || h <= 0) return;

    if (_bbCanvas.width !== Math.round(w * dpr) || _bbCanvas.height !== Math.round(h * dpr)) {
      _bbCanvas.width = Math.round(w * dpr);
      _bbCanvas.height = Math.round(h * dpr);
      _bbCanvas.style.width = w + 'px';
      _bbCanvas.style.height = h + 'px';
    }

    var ctx = _bbCanvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var timeScale = window.bigChart.timeScale();
    var plotWidth = timeScale.width();
    if (!plotWidth || isNaN(plotWidth) || plotWidth <= 0) {
      plotWidth = w;
    }

    // Determine the exact height of the main candle pane (excluding the bottom X-axis time scale)
    var plotHeight = h;
    var firstPaneTd = chartContainer.querySelector('table tr:first-child td') || chartContainer.querySelector('tr:first-child td') || chartContainer.querySelector('table tr:first-child');
    if (firstPaneTd && firstPaneTd.clientHeight > 0) {
      plotHeight = firstPaneTd.clientHeight;
    } else {
      var timeAxisTr = chartContainer.querySelector('table tr:last-child') || chartContainer.querySelector('tr:last-child');
      if (timeAxisTr && timeAxisTr.clientHeight > 0) {
        plotHeight = Math.max(0, h - timeAxisTr.clientHeight);
      } else {
        // Fallback default time axis height is 26px
        plotHeight = Math.max(0, h - 26);
      }
    }

    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, plotWidth, plotHeight);
    ctx.clip();

    var times = _candles.map(function (c) { return c.time; });

    // 1. Bollinger Bands Cloud (Blue highlight)
    if (bbInst && bbInst._lastBb && bbInst.series.upper && bbInst.series.lower) {
      var bb = bbInst._lastBb;
      var upperPts = [];
      var lowerPts = [];
      for (var i = 0; i < times.length; i++) {
        var u = bb.upper[i];
        var l = bb.lower[i];
        if (u === null || l === null || !isFinite(u) || !isFinite(l)) continue;
        var x = timeScale.timeToCoordinate(times[i]);
        if (x === null || isNaN(x)) continue;
        var yu = bbInst.series.upper.priceToCoordinate(u);
        var yl = bbInst.series.lower.priceToCoordinate(l);
        if (yu === null || yl === null || isNaN(yu) || isNaN(yl)) continue;
        upperPts.push({ x: x, y: yu });
        lowerPts.push({ x: x, y: yl });
      }
      if (upperPts.length >= 2) {
        ctx.beginPath();
        ctx.moveTo(upperPts[0].x, upperPts[0].y);
        for (var i = 1; i < upperPts.length; i++) {
          ctx.lineTo(upperPts[i].x, upperPts[i].y);
        }
        for (var i = lowerPts.length - 1; i >= 0; i--) {
          ctx.lineTo(lowerPts[i].x, lowerPts[i].y);
        }
        ctx.closePath();
        ctx.fillStyle = 'rgba(33, 150, 243, 0.08)';
        ctx.fill();
      }
    }

    // 2. VWAP Highlight Cloud (Subtle translucent green highlight between Upper and Lower bands)
    if (vwapInst && vwapInst._lastVwap && vwapInst.series.upper && vwapInst.series.lower) {
      var vwap = vwapInst._lastVwap;
      var sessions = [];
      var currentSession = [];
      var prevSKey = null;

      for (var i = 0; i < times.length; i++) {
        var u = vwap.upper[i];
        var l = vwap.lower[i];
        var sKey = Calc.candleSessionKey(_candles[i], i);
        if (sKey !== prevSKey && currentSession.length > 0) {
          sessions.push(currentSession);
          currentSession = [];
        }
        prevSKey = sKey;

        if (u === null || l === null || !isFinite(u) || !isFinite(l)) continue;
        var x = timeScale.timeToCoordinate(times[i]);
        if (x === null || isNaN(x)) continue;
        var yu = vwapInst.series.upper.priceToCoordinate(u);
        var yl = vwapInst.series.lower.priceToCoordinate(l);
        if (yu === null || yl === null || isNaN(yu) || isNaN(yl)) continue;
        currentSession.push({ x: x, yu: yu, yl: yl });
      }
      if (currentSession.length > 0) sessions.push(currentSession);

      sessions.forEach(function (sess) {
        if (sess.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(sess[0].x, sess[0].yu);
        for (var j = 1; j < sess.length; j++) {
          ctx.lineTo(sess[j].x, sess[j].yu);
        }
        for (var j = sess.length - 1; j >= 0; j--) {
          ctx.lineTo(sess[j].x, sess[j].yl);
        }
        ctx.closePath();
        ctx.fillStyle = 'rgba(76, 175, 80, 0.07)';
        ctx.fill();
      });
    }

    // 3. Supertrend Indicator (TradingView Style: Separate discrete line segments + Highlight Cloud between line & candles)
    if (stInst && stInst._lastSupertrend) {
      var stRes = stInst._lastSupertrend;
      var candleSeries = window.bigCandleSeries || (stInst.series && stInst.series.main);
      if (candleSeries && typeof candleSeries.priceToCoordinate === 'function') {
        var segments = [];
        var currentSegment = [];
        var currentTrend = null;

        for (var i = 0; i < times.length; i++) {
          var val = stRes.supertrend[i];
          var tr = stRes.trend[i];
          if (val === null || tr === null || !isFinite(val)) {
            if (currentSegment.length > 0) {
              segments.push({ trend: currentTrend, pts: currentSegment });
              currentSegment = [];
              currentTrend = null;
            }
            continue;
          }

          if (tr !== currentTrend && currentSegment.length > 0) {
            segments.push({ trend: currentTrend, pts: currentSegment });
            currentSegment = [];
          }
          currentTrend = tr;

          var x = timeScale.timeToCoordinate(times[i]);
          if (x === null || isNaN(x)) continue;
          var ySt = candleSeries.priceToCoordinate(val);
          if (ySt === null || isNaN(ySt)) continue;

          var c = _candles[i];
          var op = Number(c.open);
          var cl = Number(c.close);
          var midPrice = (isFinite(op) && isFinite(cl)) ? (op + cl) / 2.0 : (isFinite(cl) ? cl : val);
          var yMid = candleSeries.priceToCoordinate(midPrice);
          if (yMid === null || isNaN(yMid)) yMid = ySt;

          currentSegment.push({ x: x, ySt: ySt, yMid: yMid });
        }
        if (currentSegment.length > 0) {
          segments.push({ trend: currentTrend, pts: currentSegment });
        }

        segments.forEach(function (seg) {
          var pts = seg.pts;
          if (!pts || pts.length === 0) return;
          var isUp = (seg.trend === 1);
          var lineColor = isUp ? '#4FAF7B' : '#EF5350';
          var fillColor = isUp ? 'rgba(79, 175, 123, 0.14)' : 'rgba(239, 83, 80, 0.14)';

          // A. Draw Cloud Highlight Fill
          if (pts.length >= 2) {
            ctx.beginPath();
            ctx.moveTo(pts[0].x, pts[0].ySt);
            for (var j = 1; j < pts.length; j++) {
              ctx.lineTo(pts[j].x, pts[j].ySt);
            }
            for (var j = pts.length - 1; j >= 0; j--) {
              ctx.lineTo(pts[j].x, pts[j].yMid);
            }
            ctx.closePath();
            ctx.fillStyle = fillColor;
            ctx.fill();
          }

          // B. Draw Discrete Supertrend Line Segment
          if (pts.length >= 2) {
            ctx.beginPath();
            ctx.moveTo(pts[0].x, pts[0].ySt);
            for (var j = 1; j < pts.length; j++) {
              ctx.lineTo(pts[j].x, pts[j].ySt);
            }
            ctx.strokeStyle = lineColor;
            ctx.lineWidth = 2;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.stroke();
          } else if (pts.length === 1) {
            ctx.beginPath();
            ctx.arc(pts[0].x, pts[0].ySt, 1.5, 0, 2 * Math.PI);
            ctx.fillStyle = lineColor;
            ctx.fill();
          }
        });
      }
    }

    // 4. Ichimoku Kumo (cloud between Senkou Span A and Senkou Span B)
    // Multi-instance: every visible Ichimoku instance draws its own cloud,
    // independently colored/oriented.
    ichimokuInsts.forEach(function (inst) {
      if (!inst._lastIchimoku || !inst.series.spanA || !inst.series.spanB) return;
      var cloud = inst._lastIchimoku.cloud;
      if (!cloud || cloud.length === 0) return;

      var segments = [];
      var currentSegment = [];
      var currentBullish = null;
      for (var i = 0; i < cloud.length; i++) {
        var pt = cloud[i];
        var bullish = pt.spanA >= pt.spanB;
        if (bullish !== currentBullish && currentSegment.length > 0) {
          segments.push({ bullish: currentBullish, pts: currentSegment });
          currentSegment = [];
        }
        currentBullish = bullish;

        var x = timeScale.timeToCoordinate(pt.time);
        if (x === null || isNaN(x)) continue;
        var yA = inst.series.spanA.priceToCoordinate(pt.spanA);
        var yB = inst.series.spanB.priceToCoordinate(pt.spanB);
        if (yA === null || yB === null || isNaN(yA) || isNaN(yB)) continue;
        currentSegment.push({ x: x, yA: yA, yB: yB });
      }
      if (currentSegment.length > 0) segments.push({ bullish: currentBullish, pts: currentSegment });

      segments.forEach(function (seg) {
        var pts = seg.pts;
        if (pts.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].yA);
        for (var j = 1; j < pts.length; j++) ctx.lineTo(pts[j].x, pts[j].yA);
        for (var j = pts.length - 1; j >= 0; j--) ctx.lineTo(pts[j].x, pts[j].yB);
        ctx.closePath();
        ctx.fillStyle = seg.bullish ? 'rgba(79,175,123,0.14)' : 'rgba(239,83,80,0.14)';
        ctx.fill();
      });
    });

    // Pivot Points — TradingView draws each trading period's levels as a
    // DISCRETE solid horizontal segment spanning only that period's
    // candles, not one continuous line threaded through every period at
    // whatever height that period happens to be. A run of consecutive
    // candles sharing the exact same computed value for a level (they
    // will, since Calc.pivotPoints repeats one fixed value across an
    // entire period) is exactly one segment; a value change marks a new
    // period's segment starting fresh, with its own label.
    pivotInsts.forEach(function (inst) {
      if (!inst._dataByTime) return;
      var color = inst.color || '#FF9800';
      var levelDefs = [
        { key: 'r5', label: 'R5' }, { key: 'r4', label: 'R4' }, { key: 'r3', label: 'R3' }, { key: 'r2', label: 'R2' }, { key: 'r1', label: 'R1' },
        { key: 'p',  label: 'P' },
        { key: 's1', label: 'S1' }, { key: 's2', label: 'S2' }, { key: 's3', label: 'S3' }, { key: 's4', label: 'S4' }, { key: 's5', label: 'S5' }
      ];

      levelDefs.forEach(function (ld) {
        var series = inst.series[ld.key];
        if (!series) return;

        var segments = [];
        var cur = null;
        for (var i = 0; i < times.length; i++) {
          var rec = inst._dataByTime[times[i]];
          var val = rec ? rec[ld.key] : null;
          if (val === null || val === undefined || !isFinite(val)) {
            if (cur) { segments.push(cur); cur = null; }
            continue;
          }
          if (!cur || cur.value !== val) {
            if (cur) segments.push(cur);
            cur = { value: val, startIdx: i, endIdx: i };
          } else {
            cur.endIdx = i;
          }
        }
        if (cur) segments.push(cur);

        segments.forEach(function (seg) {
          var xStart = timeScale.timeToCoordinate(times[seg.startIdx]);
          var xEnd = timeScale.timeToCoordinate(times[seg.endIdx]);
          var y = series.priceToCoordinate(seg.value);
          if (xStart === null || xEnd === null || y === null || isNaN(xStart) || isNaN(xEnd) || isNaN(y)) return;

          ctx.beginPath();
          ctx.moveTo(xStart, y);
          ctx.lineTo(Math.max(xEnd, xStart + 1), y);
          ctx.strokeStyle = color;
          ctx.lineWidth = ld.key === 'p' ? 1.5 : 1;
          ctx.stroke();

          // Skip the text label on a segment too narrow to show it legibly
          // (e.g. a "Daily" pivot period viewed on a Daily+ chart, where
          // every candle IS its own period — each one's value is still
          // mathematically correct and the step-line above is still drawn,
          // but stacking a full label on every single one produces an
          // unreadable overlapping mass. TradingView's own rendering only
          // labels a segment wide enough to actually read.)
          var labelText = ld.label + ' (' + seg.value.toFixed(2) + ')';
          ctx.font = '10px sans-serif';
          var labelWidth = ctx.measureText(labelText).width;
          if ((xEnd - xStart) >= labelWidth + 6) {
            ctx.fillStyle = color;
            ctx.textBaseline = 'bottom';
            ctx.fillText(labelText, xStart + 2, y - 2);
          }
        });
      });
    });

    // Donchian Channels — translucent fill between Upper and Lower, exactly
    // matching TradingView's own indicator: `fill(u, l, color=color.rgb(33,150,243,95))`.
    // Uses each instance's OFFSET-shifted display points (_donchianDisplay,
    // built in _renderDonchian) so the fill tracks the visible lines even
    // when Offset != 0. Multiple simultaneous instances each draw their own
    // fill independently.
    donchianInsts.forEach(function (inst) {
      if (!inst._donchianDisplay || !inst.series.upper || !inst.series.lower) return;
      var upperPts = inst._donchianDisplay.upper;
      var lowerPts = inst._donchianDisplay.lower;
      if (!upperPts || !lowerPts || upperPts.length < 2 || lowerPts.length < 2) return;

      var upperCoords = [];
      for (var i = 0; i < upperPts.length; i++) {
        var x = timeScale.timeToCoordinate(upperPts[i].time);
        var y = inst.series.upper.priceToCoordinate(upperPts[i].value);
        if (x === null || y === null || isNaN(x) || isNaN(y)) continue;
        upperCoords.push({ x: x, y: y });
      }
      var lowerCoords = [];
      for (var i = 0; i < lowerPts.length; i++) {
        var x = timeScale.timeToCoordinate(lowerPts[i].time);
        var y = inst.series.lower.priceToCoordinate(lowerPts[i].value);
        if (x === null || y === null || isNaN(x) || isNaN(y)) continue;
        lowerCoords.push({ x: x, y: y });
      }
      if (upperCoords.length < 2 || lowerCoords.length < 2) return;

      ctx.beginPath();
      ctx.moveTo(upperCoords[0].x, upperCoords[0].y);
      for (var i = 1; i < upperCoords.length; i++) ctx.lineTo(upperCoords[i].x, upperCoords[i].y);
      for (var i = lowerCoords.length - 1; i >= 0; i--) ctx.lineTo(lowerCoords[i].x, lowerCoords[i].y);
      ctx.closePath();
      // A literal translation of Pine's color.rgb(33,150,243,95) (~0.37
      // alpha) reads as a heavy, oversaturated block on this canvas
      // compositing path — visually much stronger than TradingView's own
      // on-screen fill. Matched to this codebase's already-established
      // "subtle highlight" opacity instead (the same one Bollinger Bands'
      // cloud already uses), which is what the reference screenshot
      // actually looks like: a faint tint, not a solid wash.
      ctx.fillStyle = 'rgba(33, 150, 243, 0.08)';
      ctx.fill();
    });

    // Keltner Channels — translucent fill between Upper and Lower, matching
    // this codebase's existing "subtle highlight" convention (the exact
    // same opacity token Bollinger Bands' and Donchian's cloud fills use).
    // No offset support here (unlike Donchian), so this reads straight from
    // inst._lastKeltner's index-aligned arrays — the same shape as _lastBb.
    keltnerInsts.forEach(function (inst) {
      if (!inst._lastKeltner || !inst.series.upper || !inst.series.lower) return;
      var kc = inst._lastKeltner;
      var upperPts = [];
      var lowerPts = [];
      for (var i = 0; i < times.length; i++) {
        var u = kc.upper[i];
        var l = kc.lower[i];
        if (u === null || l === null || !isFinite(u) || !isFinite(l)) continue;
        var x = timeScale.timeToCoordinate(times[i]);
        if (x === null || isNaN(x)) continue;
        var yu = inst.series.upper.priceToCoordinate(u);
        var yl = inst.series.lower.priceToCoordinate(l);
        if (yu === null || yl === null || isNaN(yu) || isNaN(yl)) continue;
        upperPts.push({ x: x, y: yu });
        lowerPts.push({ x: x, y: yl });
      }
      if (upperPts.length < 2) return;
      ctx.beginPath();
      ctx.moveTo(upperPts[0].x, upperPts[0].y);
      for (var i = 1; i < upperPts.length; i++) ctx.lineTo(upperPts[i].x, upperPts[i].y);
      for (var i = lowerPts.length - 1; i >= 0; i--) ctx.lineTo(lowerPts[i].x, lowerPts[i].y);
      ctx.closePath();
      ctx.fillStyle = 'rgba(33, 150, 243, 0.08)';
      ctx.fill();
    });

    // Market Structure — BOS/CHoCH/MSS as discrete horizontal segments from
    // the origin swing to the break bar, labeled and colored by direction
    // (this project's established bullish/bearish palette, #4FAF7B /
    // #EF5350 — same as Supertrend/PSAR, not a new color choice). Solid =
    // BOS (continuation), dashed = CHoCH (reversal), thicker solid = MSS
    // (displacement-confirmed reversal) — a visual distinction beyond just
    // the text label, without needing three different colors. Uses
    // window.bigCandleSeries for price<->pixel conversion (the same
    // fallback Supertrend's own cloud rendering already relies on) since
    // events don't map to one fixed per-level series the way Pivot Points'
    // levels do.
    marketStructureInsts.forEach(function (inst) {
      if (!inst._lastMarketStructure || !window.bigCandleSeries) return;
      var msp = inst.params;
      var showBOS = msp.showBOS !== 'Off';
      var showCHoCH = msp.showCHoCH !== 'Off';
      var showMSS = msp.showMSS !== 'Off';
      var msLabel = { BOS: 'BOS', CHOCH: 'CHoCH', MSS: 'MSS' };

      inst._lastMarketStructure.events.forEach(function (ev) {
        if (ev.type === 'BOS' && !showBOS) return;
        if (ev.type === 'CHOCH' && !showCHoCH) return;
        if (ev.type === 'MSS' && !showMSS) return;

        var xStart = timeScale.timeToCoordinate(ev.originSwingTime);
        var xEnd = timeScale.timeToCoordinate(ev.breakTime);
        var y = window.bigCandleSeries.priceToCoordinate(ev.brokenLevel);
        if (xStart === null || xEnd === null || y === null || isNaN(xStart) || isNaN(xEnd) || isNaN(y)) return;

        var color = ev.direction === 'bullish' ? '#4FAF7B' : '#EF5350';

        ctx.beginPath();
        ctx.moveTo(xStart, y);
        ctx.lineTo(Math.max(xEnd, xStart + 1), y);
        ctx.strokeStyle = color;
        ctx.lineWidth = ev.type === 'MSS' ? 1.75 : 1.25;
        ctx.setLineDash(ev.type === 'CHOCH' ? [4, 3] : []);
        ctx.stroke();
        ctx.setLineDash([]);

        var labelText = msLabel[ev.type];
        ctx.font = '10px sans-serif';
        var labelWidth = ctx.measureText(labelText).width;
        if ((xEnd - xStart) >= labelWidth + 6) {
          ctx.fillStyle = color;
          ctx.textBaseline = 'bottom';
          ctx.fillText(labelText, xStart + (xEnd - xStart) / 2 - labelWidth / 2, y - 3);
        }
      });
    });

    // Fair Value Gap — each gap rendered as a filled rectangle (top/bottom
    // price, left/right time), reusing the same canvas + coordinate
    // conversion technique as every fill/segment above. Extend/mitigated
    // display state is decided HERE, not inside Calc.fvg — the
    // calculation only reports facts (status, mitigatedIndex,
    // fillPercentage); how far a still-active zone visually extends, or
    // whether a mitigated one is shown at all, is pure display policy,
    // the same separation Market Structure's showBOS/CHoCH/MSS filters
    // already use.
    fvgInsts.forEach(function (inst) {
      if (!inst._lastFVG || !window.bigCandleSeries) return;
      var fp = inst.params;
      var showBullish = fp.showBullish !== 'Off';
      var showBearish = fp.showBearish !== 'Off';
      var showMitigated = fp.showMitigated !== 'Off';
      var extend = fp.extendZones !== 'Off';
      var extendBars = parseInt(fp.extendBars, 10);
      if (isNaN(extendBars) || extendBars < 1) extendBars = 25;
      var showMid = fp.showMidpoint === 'On';
      var lastIndex = _candles.length - 1;

      inst._lastFVG.forEach(function (fvg) {
        if (fvg.direction === 'bullish' && !showBullish) return;
        if (fvg.direction === 'bearish' && !showBearish) return;
        var mitigated = (fvg.status === 'fully_mitigated');
        if (mitigated && !showMitigated) return;

        // A still-active zone extends by a FIXED number of bars past its
        // own confirmation candle, not indefinitely to the current bar —
        // stretching every old active gap all the way to "now" is what
        // caused long-lived zones to visually swallow unrelated later
        // price action (reported directly from the live chart). Matches
        // TradingView's own FVG scripts, which extend by a bounded bar
        // count, not to the live edge.
        var endTime = mitigated
          ? _candles[fvg.mitigatedIndex].time
          : (extend ? _candles[Math.min(fvg.confirmIndex + extendBars, lastIndex)].time : fvg.confirmTime);
        var xStart = timeScale.timeToCoordinate(fvg.startTime);
        var xEnd = timeScale.timeToCoordinate(endTime);
        var yTop = window.bigCandleSeries.priceToCoordinate(fvg.top);
        var yBottom = window.bigCandleSeries.priceToCoordinate(fvg.bottom);
        if (xStart === null || xEnd === null || yTop === null || yBottom === null ||
            isNaN(xStart) || isNaN(xEnd) || isNaN(yTop) || isNaN(yBottom)) return;
        var xRight = Math.max(xEnd, xStart + 1);

        // Rich TradingView bullish/bearish palette (#089981 / #F23645) with increased darkness/opacity
        var baseColor = fvg.direction === 'bullish' ? '8, 153, 129' : '242, 54, 69';
        var alpha = mitigated ? 0.12 : 0.35;
        ctx.fillStyle = 'rgba(' + baseColor + ', ' + alpha + ')';
        ctx.fillRect(xStart, yTop, xRight - xStart, yBottom - yTop);

        if (showMid) {
          var yMid = window.bigCandleSeries.priceToCoordinate(fvg.midpoint);
          if (yMid !== null && !isNaN(yMid)) {
            ctx.beginPath();
            ctx.moveTo(xStart, yMid);
            ctx.lineTo(xRight, yMid);
            ctx.strokeStyle = 'rgba(' + baseColor + ', 0.6)';
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        }
      });
    });

    // Order Blocks — same filled-rectangle technique as FVG, but the
    // extend/recolor rules deliberately do NOT mirror FVG's fixed-length
    // extension (per TradingView's own reference Order Blocks script: a box
    // is created with extend=extend.right and keeps growing to the live
    // edge for its entire life; mitigation only calls box.set_bgcolor to a
    // dedicated neutral grey — it does NOT stop the box. Only a decisive
    // close through the opposite boundary calls box.delete, which is the
    // ONLY thing that freezes a box's right edge). So here: 'active' AND
    // 'fully_mitigated' both extend to the live edge; only 'invalidated'
    // freezes at the bar it broke.
    //
    // "Live edge" means the canvas' own right edge (`w`, in CSS pixels —
    // this canvas is already dpr-transformed above), NOT
    // timeToCoordinate(lastCandleTime): the chart always reserves some
    // empty right-margin space past the last real candle (LWC's own
    // rightOffset), and stopping at the last candle's coordinate leaves
    // that margin unfilled — reported directly ("it have to be infinite
    // like tradingview") since TradingView's extend.right box genuinely
    // reaches the viewport edge, not just the last bar.
    //
    // Colors/alphas are ported from the reference script's own inputs —
    // bearishOBColour rgb(219,166,50), bullishOBColour rgb(192,230,174),
    // mitigatedOBColour rgb(207,203,202) — rather than this codebase's
    // own green/red convention, so a faded/mitigated block reads as a
    // genuinely distinct neutral tone, not just a dimmer bullish/bearish
    // hue. Each block also draws a visibly DARKER "core" band across the
    // origin candle's own actual High/Low, on top of the lighter full
    // zone: top/bottom can extend beyond that candle (see
    // Calc.orderBlocks' docstring — top keeps climbing with the up-leg
    // for a bearish block, bottom keeps falling with the down-leg for a
    // bullish one), so without this the anchor candle disappears inside
    // a taller, uniformly-faint zone (reported directly: "i see block
    // which build in of any highlight block" — no visual way to tell the
    // origin candle apart from its own extension).
    obInsts.forEach(function (inst) {
      if (!inst._lastOB || !window.bigCandleSeries) return;
      var op = inst.params;
      var showBullishOB = op.showBullish !== 'Off';
      var showBearishOB = op.showBearish !== 'Off';
      var showMitigatedOB = op.showMitigated === 'On';
      var extendOB = op.extendZones !== 'Off';
      var showMidOB = op.showMidpoint === 'On';
      var maxZ = parseInt(op.maxZones, 10) || 5;

      var activeBullish = [];
      var activeBearish = [];
      var mitigatedObs = [];
      var invalidatedObs = [];

      inst._lastOB.forEach(function (ob) {
        if (ob.status === 'active' || ob.status === 'partially_mitigated') {
          if (ob.direction === 'bullish') activeBullish.push(ob);
          else activeBearish.push(ob);
        } else if (ob.status === 'fully_mitigated') {
          mitigatedObs.push(ob);
        } else if (ob.status === 'invalidated') {
          invalidatedObs.push(ob);
        }
      });

      var visibleOBs = [];
      if (showBullishOB) visibleOBs = visibleOBs.concat(activeBullish.slice(-maxZ));
      if (showBearishOB) visibleOBs = visibleOBs.concat(activeBearish.slice(-maxZ));
      if (showMitigatedOB) {
        visibleOBs = visibleOBs.concat(mitigatedObs.slice(-maxZ));
        visibleOBs = visibleOBs.concat(invalidatedObs.slice(-maxZ));
      }
      visibleOBs.sort(function (a, b) { return a.originIndex - b.originIndex; });

      visibleOBs.forEach(function (ob) {
        if (ob.direction === 'bullish' && !showBullishOB) return;
        if (ob.direction === 'bearish' && !showBearishOB) return;
        var mitigatedOB = (ob.status === 'fully_mitigated');
        var invalidatedOB = (ob.status === 'invalidated');
        var terminal = mitigatedOB || invalidatedOB;
        if (terminal && !showMitigatedOB) return;

        var xStart = timeScale.timeToCoordinate(ob.startTime);
        var yTop = window.bigCandleSeries.priceToCoordinate(ob.top);
        var yBottom = window.bigCandleSeries.priceToCoordinate(ob.bottom);
        if (yTop === null || yBottom === null || isNaN(yTop) || isNaN(yBottom)) return;

        // If xStart is null/NaN or before the visible screen (scrolled to recent bars),
        // clamp xStart to 0 so the active/mitigated zone remains visible across the chart canvas.
        if (xStart === null || isNaN(xStart) || xStart < 0) {
          xStart = 0;
        }

        var xRightOB;
        if (invalidatedOB) {
          var xEndInvalidated = (ob.invalidatedIndex !== null && _candles[ob.invalidatedIndex]) ?
            timeScale.timeToCoordinate(_candles[ob.invalidatedIndex].time) : null;
          if (xEndInvalidated === null || isNaN(xEndInvalidated)) {
            if (ob.invalidatedIndex !== null && ob.invalidatedIndex < _candles.length - 1) return;
            xEndInvalidated = plotWidth;
          }
          if (xEndInvalidated <= 0) return;
          xRightOB = Math.min(plotWidth, Math.max(xEndInvalidated, xStart + 1));
        } else if (mitigatedOB) {
          var xEndMitigated = (ob.mitigatedIndex !== null && _candles[ob.mitigatedIndex]) ?
            timeScale.timeToCoordinate(_candles[ob.mitigatedIndex].time) : null;
          if (xEndMitigated === null || isNaN(xEndMitigated)) {
            var xEndConfirm = timeScale.timeToCoordinate(ob.confirmTime);
            xEndMitigated = (xEndConfirm !== null && !isNaN(xEndConfirm)) ? xEndConfirm : (xStart + 1);
          }
          if (extendOB) {
            xRightOB = plotWidth;
          } else {
            if (xEndMitigated <= 0) return;
            xRightOB = Math.min(plotWidth, Math.max(xEndMitigated, xStart + 1));
          }
        } else if (extendOB) {
          xRightOB = plotWidth;
        } else {
          var xEndConfirm = timeScale.timeToCoordinate(ob.confirmTime);
          if (xEndConfirm === null || isNaN(xEndConfirm)) {
            xRightOB = plotWidth;
          } else {
            if (xEndConfirm <= 0) return;
            xRightOB = Math.min(plotWidth, Math.max(xEndConfirm, xStart + 1));
          }
        }

        var obFillStyle, coreFillStyle, borderStyle;
        if (invalidatedOB) {
          obFillStyle = 'rgba(207, 203, 202, 0.05)';
          coreFillStyle = 'rgba(207, 203, 202, 0.10)';
          borderStyle = 'rgba(207, 203, 202, 0.20)';
        } else if (mitigatedOB) {
          obFillStyle = 'rgba(207, 203, 202, 0.08)';
          coreFillStyle = 'rgba(207, 203, 202, 0.16)';
          borderStyle = 'rgba(207, 203, 202, 0.30)';
        } else if (ob.direction === 'bullish') {
          // TradingView Bullish OB: exact sampled colors from reference
          obFillStyle = '#56654f';
          coreFillStyle = '#819975';
          borderStyle = '#91a986';
        } else {
          // TradingView Bearish OB: exact sampled colors from reference
          obFillStyle = '#382d16';
          coreFillStyle = '#59451c';
          borderStyle = '#766238';
        }

        ctx.fillStyle = obFillStyle;
        ctx.fillRect(xStart, yTop, xRightOB - xStart, yBottom - yTop);

        var yCoreTop = window.bigCandleSeries.priceToCoordinate(ob.originHigh);
        var yCoreBottom = window.bigCandleSeries.priceToCoordinate(ob.originLow);
        if (yCoreTop !== null && yCoreBottom !== null && !isNaN(yCoreTop) && !isNaN(yCoreBottom)) {
          ctx.fillStyle = coreFillStyle;
          ctx.fillRect(xStart, yCoreTop, xRightOB - xStart, yCoreBottom - yCoreTop);
        }

        // Crisp top and bottom boundary lines
        ctx.strokeStyle = borderStyle;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(xStart, yTop);
        ctx.lineTo(xRightOB, yTop);
        ctx.moveTo(xStart, yBottom);
        ctx.lineTo(xRightOB, yBottom);
        ctx.stroke();

        if (showMidOB) {
          var yMidOB = window.bigCandleSeries.priceToCoordinate(ob.midpoint);
          if (yMidOB !== null && !isNaN(yMidOB)) {
            ctx.beginPath();
            ctx.moveTo(xStart, yMidOB);
            ctx.lineTo(xRightOB, yMidOB);
            ctx.strokeStyle = terminal ? 'rgba(207, 203, 202, 0.4)' : (ob.direction === 'bullish' ? '#91a986' : '#766238');
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        }
      });
    });

    // Liquidity — a horizontal LEVEL per pool (line, not a filled zone —
    // pools are price levels, not ranges). BUY_SIDE reuses Donchian's
    // established blue (#2962FF), SELL_SIDE reuses Pivot Points'
    // established orange (#FF9800) — both already-existing palette
    // tokens, not new colors. Swept pools (always false/unset in this
    // phase, reserved for Phase 2T) render dashed once populated.
    liquidityInsts.forEach(function (inst) {
      if (!inst._lastLiquidity || !window.bigCandleSeries) return;
      var lp = inst.params;
      var showEQH = lp.showEqualHighs !== 'Off';
      var showEQL = lp.showEqualLows !== 'Off';
      var showSwingHigh = lp.showSwingHighLiquidity === 'On';
      var showSwingLow = lp.showSwingLowLiquidity === 'On';
      var showLabels = lp.showLabels !== 'Off';
      var showSwept = lp.showSweptLevels !== 'Off';
      var extendLiq = lp.extendLevels !== 'Off';
      var lastTimeLiq = times.length ? times[times.length - 1] : null;
      var poolLabel = { EQUAL_HIGH: 'EQH', EQUAL_LOW: 'EQL', SWING_HIGH: 'BSL', SWING_LOW: 'SSL' };

      function formatVol(vol) {
        if (!vol || isNaN(vol)) return '';
        if (vol >= 1e9) return (vol / 1e9).toFixed(1) + 'B';
        if (vol >= 1e6) return (vol / 1e6).toFixed(1) + 'M';
        if (vol >= 1e3) return (vol / 1e3).toFixed(1) + 'K';
        return String(Math.round(vol));
      }

      inst._lastLiquidity.forEach(function (pool) {
        if (pool.type === 'EQUAL_HIGH' && !showEQH) return;
        if (pool.type === 'EQUAL_LOW' && !showEQL) return;
        if (pool.type === 'SWING_HIGH' && !showSwingHigh) return;
        if (pool.type === 'SWING_LOW' && !showSwingLow) return;

        // Check swept status if not already set
        var isHigh = (pool.type === 'EQUAL_HIGH' || pool.type === 'SWING_HIGH');
        var isSwept = pool.swept;
        var sweptAt = pool.sweptAt;
        if (!isSwept && _candles) {
          for (var k = pool.latestTouchIndex + 1; k < _candles.length; k++) {
            var candH = Number(_candles[k].high);
            var candL = Number(_candles[k].low);
            if (isHigh && isFinite(candH) && candH > pool.price) {
              isSwept = true;
              sweptAt = _candles[k].time;
              break;
            }
            if (!isHigh && isFinite(candL) && candL < pool.price) {
              isSwept = true;
              sweptAt = _candles[k].time;
              break;
            }
          }
        }

        if (isSwept && !showSwept) return;

        var isHigh = (pool.type === 'EQUAL_HIGH' || pool.type === 'SWING_HIGH');
        var isSwept = pool.swept;
        var sweptAt = pool.sweptAt;
        if (!isSwept && _candles) {
          for (var k = pool.latestTouchIndex + 1; k < _candles.length; k++) {
            var candH = Number(_candles[k].high);
            var candL = Number(_candles[k].low);
            if (isHigh && isFinite(candH) && candH > pool.price) {
              isSwept = true;
              sweptAt = _candles[k].time;
              break;
            }
            if (!isHigh && isFinite(candL) && candL < pool.price) {
              isSwept = true;
              sweptAt = _candles[k].time;
              break;
            }
          }
        }

        if (isSwept && !showSwept) return;

        var activeColor = isHigh ? '#F23645' : '#089981';
        var activeFill = isHigh ? 'rgba(242, 54, 69, 0.85)' : 'rgba(8, 153, 129, 0.85)';
        var sweptColor = 'rgba(130, 133, 140, 0.75)';
        var sweptFill = 'rgba(130, 133, 140, 0.40)';

        var liqColor = isSwept ? sweptColor : activeColor;
        var circleFill = isSwept ? sweptFill : activeFill;
        var circleStroke = isSwept ? 'rgba(130, 133, 140, 0.6)' : activeColor;

        // 1. Draw touch point discs (circles at each swing high/low) with individual candle volume text
        if (pool.touchPoints && pool.touchPoints.length) {
          pool.touchPoints.forEach(function (tp) {
            var xTp = timeScale.timeToCoordinate(tp.time);
            var yTp = window.bigCandleSeries.priceToCoordinate(tp.price);
            if (xTp !== null && yTp !== null && !isNaN(xTp) && !isNaN(yTp)) {
              ctx.beginPath();
              ctx.arc(xTp, yTp, 8, 0, 2 * Math.PI);
              ctx.fillStyle = circleFill;
              ctx.fill();
              ctx.strokeStyle = circleStroke;
              ctx.lineWidth = 1;
              ctx.stroke();

              // Draw individual candle volume
              if (_candles && _candles[tp.index] && _candles[tp.index].volume) {
                var tpVolStr = formatVol(_candles[tp.index].volume);
                if (tpVolStr) {
                  ctx.save();
                  ctx.font = 'bold 9px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                  ctx.fillStyle = isSwept ? 'rgba(130, 133, 140, 0.7)' : (isHigh ? '#F23645' : '#089981');
                  ctx.textAlign = 'center';
                  if (isHigh) {
                    ctx.textBaseline = 'bottom';
                    ctx.fillText(tpVolStr, xTp, yTp - 10);
                  } else {
                    ctx.textBaseline = 'top';
                    ctx.fillText(tpVolStr, xTp, yTp + 10);
                  }
                  ctx.restore();
                }
              }
            }
          });
        }

        // 2. Determine end coordinate for horizontal level line
        var endTimeLiq;
        if (isSwept && sweptAt) {
          endTimeLiq = sweptAt;
        } else if (extendLiq) {
          endTimeLiq = lastTimeLiq;
        } else {
          endTimeLiq = pool.latestTouchTime;
        }

        var xStartLiq = timeScale.timeToCoordinate(pool.firstTouchTime);
        var xEndLiq = timeScale.timeToCoordinate(endTimeLiq);
        var yLiq = window.bigCandleSeries.priceToCoordinate(pool.price);
        if (yLiq === null || isNaN(yLiq)) return;
        if (xEndLiq === null || isNaN(xEndLiq)) {
          if (extendLiq) xEndLiq = plotWidth;
          else return;
        }
        if (xStartLiq === null || isNaN(xStartLiq) || xStartLiq < 0) {
          xStartLiq = 0;
        }
        if (xEndLiq <= 0) return;
        var xRightLiq = Math.min(plotWidth, Math.max(xEndLiq, xStartLiq + 1));

        ctx.beginPath();
        ctx.moveTo(xStartLiq, yLiq);
        ctx.lineTo(xRightLiq, yLiq);
        ctx.strokeStyle = liqColor;
        ctx.lineWidth = pool.touchCount > 1 ? 1.5 : 1;
        ctx.setLineDash(isSwept ? [4, 3] : []);
        ctx.stroke();
        ctx.setLineDash([]);

        // 3. Draw label
        if (showLabels) {
          var prefix = isSwept ? 'Swept ' : '';
          var baseName = poolLabel[pool.type] || pool.type;
          var liqLabelText = prefix + baseName;

          // Calculate total volume across touch points if available
          var totalVol = 0;
          if (pool.touchPoints && pool.touchPoints.length && _candles) {
            pool.touchPoints.forEach(function (tp) {
              if (_candles[tp.index] && _candles[tp.index].volume) {
                totalVol += Number(_candles[tp.index].volume) || 0;
              }
            });
          }
          var vStr = formatVol(totalVol);
          if (vStr) {
            liqLabelText += ' (' + vStr + ')';
          }

          ctx.save();
          ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
          ctx.fillStyle = liqColor;
          if (isSwept) {
            // Swept labels along the dashed segment
            var midX = (xStartLiq + xRightLiq) / 2;
            ctx.textAlign = 'center';
            ctx.textBaseline = isHigh ? 'bottom' : 'top';
            ctx.fillText(liqLabelText, midX, isHigh ? (yLiq - 3) : (yLiq + 3));
          } else {
            // Active labels at the right edge, matching TradingView LuxAlgo
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText(liqLabelText, xRightLiq - 4, yLiq - 3);
          }
          ctx.restore();
        }
      });
    });

    // Liquidity Sweeps — TradingView LuxAlgo Liquidity Sweeps model:
    // 1. Pivot ring marker at the original liquidity anchor (blue ring)
    // 2. Dotted horizontal line connecting original pivot level to the sweeping candle
    // 3. Shaded sweep zone rectangle (box) spanning from the sweep candle across to subsequent candles:
    //    - Bearish sweep: red box between the swept resistance level and the highest wick (sweepExtreme)
    //    - Bullish sweep: teal-green box between the swept support level and the lowest wick (sweepExtreme)
    // 4. Sweep label & invalidation tracking
    liquiditySweepInsts.forEach(function (inst) {
      if (!inst._lastLiquiditySweeps || !window.bigCandleSeries) return;
      var sp = inst.params;
      var showBuySideSweep = sp.showBuySide !== 'Off';
      var showSellSideSweep = sp.showSellSide !== 'Off';
      var showSweepLabels = sp.showLabels !== 'Off';

      inst._lastLiquiditySweeps.forEach(function (sweep) {
        if (sweep.sweepType === 'BUY_SIDE_SWEEP' && !showBuySideSweep) return;
        if (sweep.sweepType === 'SELL_SIDE_SWEEP' && !showSellSideSweep) return;

        var isBearish = (sweep.direction === 'bearish');
        var sweepColor = isBearish ? '#F23645' : '#089981';
        var fillColor = isBearish ? 'rgba(242, 54, 69, 0.28)' : 'rgba(8, 153, 129, 0.28)';
        var borderColor = isBearish ? 'rgba(242, 54, 69, 0.65)' : 'rgba(8, 153, 129, 0.65)';

        var yLevel = window.bigCandleSeries.priceToCoordinate(sweep.liquidityPrice);
        var yExtreme = window.bigCandleSeries.priceToCoordinate(sweep.sweepExtreme);
        if (yLevel === null || yExtreme === null || isNaN(yLevel) || isNaN(yExtreme)) return;

        var xAnchor = sweep.anchorTime ? timeScale.timeToCoordinate(sweep.anchorTime) : null;
        var xStartSweep = timeScale.timeToCoordinate(sweep.sweepTime);

        // Determine how far the sweep box extends to the right
        var extendBars = 25;
        var lastIdx = _candles.length - 1;
        var isRecent = (sweep.sweepIndex + extendBars >= lastIdx);
        var xEndBox = null;

        if (_candles && sweep.sweepIndex !== undefined) {
          for (var k = sweep.sweepIndex + 1; k < _candles.length; k++) {
            var closeK = Number(_candles[k].close);
            if (isBearish && isFinite(closeK) && closeK > sweep.sweepExtreme) {
              xEndBox = timeScale.timeToCoordinate(_candles[k].time);
              break;
            }
            if (!isBearish && isFinite(closeK) && closeK < sweep.sweepExtreme) {
              xEndBox = timeScale.timeToCoordinate(_candles[k].time);
              break;
            }
          }
        }
        if (xEndBox === null || isNaN(xEndBox)) {
          if (isRecent) {
            xEndBox = plotWidth;
          } else {
            var boundedTime = _candles[Math.min(sweep.sweepIndex + extendBars, lastIdx)].time;
            xEndBox = timeScale.timeToCoordinate(boundedTime);
          }
        }

        // 1. Draw small blue pivot ring at original anchor point (if within visible range)
        if (xAnchor !== null && !isNaN(xAnchor) && xAnchor >= -50 && xAnchor <= plotWidth + 50) {
          ctx.beginPath();
          ctx.arc(xAnchor, yLevel, 3.5, 0, 2 * Math.PI);
          ctx.strokeStyle = '#2962FF';
          ctx.lineWidth = 1.5;
          ctx.stroke();

          // 2. Dotted horizontal line connecting anchor to the sweeping candle
          var xLineEnd = (xStartSweep !== null && !isNaN(xStartSweep)) ? xStartSweep : (xEndBox !== null ? xEndBox : plotWidth);
          if (xLineEnd > xAnchor) {
            ctx.beginPath();
            ctx.moveTo(xAnchor, yLevel);
            ctx.lineTo(xLineEnd, yLevel);
            ctx.strokeStyle = borderColor;
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 3]);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        }

        // Viewport culling for sweep box
        if (xStartSweep === null && xEndBox === null) return;
        if (xStartSweep !== null && xStartSweep > plotWidth + 50) return;
        if (xEndBox !== null && xEndBox < -50) return;

        var xLeft = (xStartSweep !== null && !isNaN(xStartSweep)) ? Math.max(0, xStartSweep) : 0;
        var xRight = (xEndBox !== null && !isNaN(xEndBox)) ? Math.min(plotWidth, xEndBox) : plotWidth;

        // 3. Draw the filled sweep rectangle (box) and borders as thin clean bands (matching LuxAlgo)
        var rawH = Math.abs(yLevel - yExtreme);
        var boxH = Math.max(3, Math.min(18, rawH));
        var boxY = isBearish ? Math.min(yLevel, yExtreme) : (Math.max(yLevel, yExtreme) - boxH);
        var boxW = xRight - xLeft;
        if (boxW > 0 && xLeft < plotWidth && xRight > 0) {
          ctx.fillStyle = fillColor;
          ctx.fillRect(xLeft, boxY, boxW, boxH);
          ctx.strokeStyle = borderColor;
          ctx.lineWidth = 1;
          ctx.strokeRect(xLeft, boxY, boxW, boxH);
        }

        // 4. Draw label
        if (showSweepLabels && xLeft >= 0 && xLeft < plotWidth) {
          var sweepLabelText = isBearish ? 'Swept EQH' : 'Swept EQL';
          if (sweep.sweepDepth) {
            sweepLabelText += ' (' + sweep.sweepDepth.toFixed(2) + ')';
          }
          ctx.save();
          ctx.font = 'bold 9px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
          ctx.fillStyle = sweepColor;
          ctx.textAlign = 'left';
          ctx.textBaseline = isBearish ? 'bottom' : 'top';
          ctx.fillText(sweepLabelText, xLeft + 4, isBearish ? (boxY - 3) : (boxY + boxH + 3));
          ctx.restore();
        }
      });
    });

    // Premium & Discount Delta Volume (LuxAlgo style)
    // - Macro dealing range bands: top brown/tan band and bottom slate/teal band with volume deltas
    // - Internal dealing range box:
    //   - Top pill/header: PREMIUM: -2.453M
    //   - Bottom pill/footer: DISCOUNT: 2.3M
    //   - Dashed 50% Equilibrium line
    //   - Subtle Premium (red) and Discount (teal) zone fills
    //   - Delta Volume percentage badge inside the box
    premiumDiscountInsts.forEach(function (inst) {
      if (!inst._lastPremiumDiscount || !window.bigCandleSeries) return;
      var pd = inst.params;
      var range = inst._lastPremiumDiscount.ranges.length
        ? inst._lastPremiumDiscount.ranges[inst._lastPremiumDiscount.ranges.length - 1]
        : null;
      if (!range) return;

      var showPremiumZone = pd.showPremium !== 'Off';
      var showDiscountZone = pd.showDiscount !== 'Off';
      var showEquilibriumLine = pd.showEquilibrium !== 'Off';
      var showRangeLines = pd.showDealingRange !== 'Off';
      var showDeltaVolume = pd.showDeltaVolume !== 'Off';
      var showMacroRange = pd.showMacroRange !== 'Off';
      var opacity = parseFloat(pd.zoneOpacity);
      if (isNaN(opacity) || opacity < 0) opacity = 0.08;

      var macroRange = inst._lastPremiumDiscount.macroRange;

      function fmtVol(v) {
        if (v === undefined || v === null || isNaN(v)) return '0';
        var sign = v < 0 ? '-' : '';
        var abs = Math.abs(v);
        if (abs >= 1e9) {
          var num = abs / 1e9;
          var str = num >= 10 ? num.toFixed(1) : num.toFixed(3);
          return sign + str.replace(/\.?0+$/, '') + 'B';
        }
        if (abs >= 1e6) {
          var num = abs / 1e6;
          var str = num >= 10 ? num.toFixed(1) : num.toFixed(3);
          return sign + str.replace(/\.?0+$/, '') + 'M';
        }
        if (abs >= 1e3) {
          var num = abs / 1e3;
          var str = num >= 10 ? num.toFixed(1) : num.toFixed(1);
          return sign + str.replace(/\.?0+$/, '') + 'K';
        }
        return sign + Math.round(abs).toString();
      }

      // 1. Draw Macro Dealing Range Bands (if enabled and present)
      if (showMacroRange && macroRange) {
        var xMacroHigh = timeScale.timeToCoordinate(macroRange.highTime);
        var yMacroHigh = window.bigCandleSeries.priceToCoordinate(macroRange.rangeHigh);
        var xMacroLow = timeScale.timeToCoordinate(macroRange.lowTime);
        var yMacroLow = window.bigCandleSeries.priceToCoordinate(macroRange.rangeLow);

        var bandH = 10;
        var rightEdge = plotWidth;

        // Top Brown Macro Band
        if (yMacroHigh !== null && !isNaN(yMacroHigh) && xMacroHigh !== null && !isNaN(xMacroHigh)) {
          var startX = Math.max(0, Math.min(plotWidth, xMacroHigh));
          ctx.save();
          ctx.fillStyle = 'rgba(141, 110, 99, 0.45)';
          ctx.fillRect(startX, yMacroHigh - bandH / 2, rightEdge - startX, bandH);
          ctx.strokeStyle = 'rgba(141, 110, 99, 0.85)';
          ctx.lineWidth = 1;
          ctx.strokeRect(startX, yMacroHigh - bandH / 2, rightEdge - startX, bandH);

          var topVolLabel = fmtVol(macroRange.highDelta);
          ctx.font = 'bold 10px sans-serif';
          ctx.fillStyle = '#d7ccc8';
          ctx.textAlign = 'right';
          ctx.textBaseline = 'middle';
          ctx.fillText(topVolLabel, rightEdge - 6, yMacroHigh);
          ctx.restore();
        }

        // Bottom Slate Teal/Blue Macro Band
        if (yMacroLow !== null && !isNaN(yMacroLow) && xMacroLow !== null && !isNaN(xMacroLow)) {
          var startXLow = Math.max(0, Math.min(plotWidth, xMacroLow));
          ctx.save();
          ctx.fillStyle = 'rgba(84, 110, 122, 0.45)';
          ctx.fillRect(startXLow, yMacroLow - bandH / 2, rightEdge - startXLow, bandH);
          ctx.strokeStyle = 'rgba(84, 110, 122, 0.85)';
          ctx.lineWidth = 1;
          ctx.strokeRect(startXLow, yMacroLow - bandH / 2, rightEdge - startXLow, bandH);

          var lowVolLabel = fmtVol(macroRange.lowDelta);
          ctx.font = 'bold 10px sans-serif';
          ctx.fillStyle = '#cfd8dc';
          ctx.textAlign = 'right';
          ctx.textBaseline = 'middle';
          ctx.fillText(lowVolLabel, rightEdge - 6, yMacroLow);
          ctx.restore();
        }
      }

      // 2. Draw Internal Dealing Range Box
      var startTimePD = _candles[Math.min(range.highIndex, range.lowIndex)].time;
      var xStartPD = timeScale.timeToCoordinate(startTimePD);
      var xEndPD = timeScale.timeToCoordinate(times[times.length - 1]);
      var yHigh = window.bigCandleSeries.priceToCoordinate(range.rangeHigh);
      var yEq = window.bigCandleSeries.priceToCoordinate(range.equilibrium);
      var yLow = window.bigCandleSeries.priceToCoordinate(range.rangeLow);
      if (xStartPD === null || xEndPD === null || yHigh === null || yEq === null || yLow === null ||
          isNaN(xStartPD) || isNaN(xEndPD) || isNaN(yHigh) || isNaN(yEq) || isNaN(yLow)) return;
      var xRightPD = Math.max(xEndPD, plotWidth);

      if (showPremiumZone) {
        ctx.fillStyle = 'rgba(239, 83, 80, ' + opacity + ')';
        ctx.fillRect(xStartPD, yHigh, xRightPD - xStartPD, yEq - yHigh);
      }
      if (showDiscountZone) {
        ctx.fillStyle = 'rgba(38, 166, 154, ' + opacity + ')';
        ctx.fillRect(xStartPD, yEq, xRightPD - xStartPD, yLow - yEq);
      }
      if (showEquilibriumLine) {
        ctx.beginPath();
        ctx.moveTo(xStartPD, yEq);
        ctx.lineTo(xRightPD, yEq);
        ctx.strokeStyle = '#787b86';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      if (showRangeLines) {
        // Outer box border
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(120, 123, 134, 0.4)';
        ctx.lineWidth = 1;
        ctx.strokeRect(xStartPD, yHigh, xRightPD - xStartPD, yLow - yHigh);

        // Top Header Pill: PREMIUM: <vol>
        var activeVol = inst._lastPremiumDiscount.activeVolume;
        var premDelta = activeVol && activeVol.premiumDelta !== undefined ? activeVol.premiumDelta : range.premiumDelta;
        if (premDelta === undefined || premDelta === null) premDelta = range.deltaVolume;
        var premText = 'PREMIUM: ' + fmtVol(premDelta);
        var pillH = 14;

        ctx.save();
        ctx.fillStyle = 'rgba(35, 20, 20, 0.55)';
        ctx.fillRect(xStartPD, yHigh, xRightPD - xStartPD, pillH);
        ctx.strokeStyle = 'rgba(141, 110, 99, 0.55)';
        ctx.strokeRect(xStartPD, yHigh, xRightPD - xStartPD, pillH);
        ctx.font = 'bold 9px sans-serif';
        ctx.fillStyle = '#e0e0e0';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(premText, (xStartPD + xRightPD) / 2, yHigh + pillH / 2);
        ctx.restore();

        // Bottom Footer Pill: DISCOUNT: <vol>
        var discDelta = activeVol && activeVol.discountDelta !== undefined ? activeVol.discountDelta : range.discountDelta;
        if (discDelta === undefined || discDelta === null) discDelta = range.totalVolume;
        var discText = 'DISCOUNT: ' + fmtVol(discDelta);

        ctx.save();
        ctx.fillStyle = 'rgba(20, 35, 35, 0.55)';
        ctx.fillRect(xStartPD, yLow - pillH, xRightPD - xStartPD, pillH);
        ctx.strokeStyle = 'rgba(84, 110, 122, 0.55)';
        ctx.strokeRect(xStartPD, yLow - pillH, xRightPD - xStartPD, pillH);
        ctx.font = 'bold 9px sans-serif';
        ctx.fillStyle = '#e0e0e0';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(discText, (xStartPD + xRightPD) / 2, yLow - pillH / 2);
        ctx.restore();
      }

      // 3. Delta Volume Label inside the box
      if (showDeltaVolume) {
        var activeVol = inst._lastPremiumDiscount.activeVolume;
        var dPct = activeVol && activeVol.deltaVolumePct !== undefined ? activeVol.deltaVolumePct : (range.deltaVolumePct || 0);
        var isBullDelta = dPct >= 0;
        var deltaColor = isBullDelta ? '#64b5f6' : '#ff8a65';
        var pctStr = (isBullDelta ? '+' : '') + dPct.toFixed(2) + '%';

        ctx.save();
        ctx.textAlign = 'right';
        var textX = xRightPD - 24;
        var textY = yLow - 36;
        if (textY > yEq + 10) {
          ctx.font = '12px sans-serif';
          ctx.fillStyle = deltaColor;
          ctx.fillText('Delta Volume', textX, textY);
          ctx.font = 'bold 13px sans-serif';
          ctx.fillText(pctStr, textX, textY + 16);
        }
        ctx.restore();
      }
    });

    // SMC Setups — a compact marker + score label at each confirmed
    // setup's structural confirmation point (structureEvent.breakIndex),
    // colored by direction using this codebase's established bullish/
    // bearish palette. Deliberately minimal (one small circle + one short
    // label per setup) since these are meant to be rare, high-value
    // composite events, not a dense per-candle signal.
    smcInsts.forEach(function (inst) {
      if (!inst._lastSMC || !window.bigCandleSeries) return;
      var smcP = inst.params;
      var showBullishSMC = smcP.showBullish !== 'Off';
      var showBearishSMC = smcP.showBearish !== 'Off';
      var showSMCLabels = smcP.showLabels !== 'Off';

      inst._lastSMC.forEach(function (setup) {
        if (setup.direction === 'bullish' && !showBullishSMC) return;
        if (setup.direction === 'bearish' && !showBearishSMC) return;

        var xSMC = timeScale.timeToCoordinate(setup.time);
        var ySMC = window.bigCandleSeries.priceToCoordinate(setup.liquidityPrice);
        if (xSMC === null || ySMC === null || isNaN(xSMC) || isNaN(ySMC)) return;

        var smcColor = setup.direction === 'bullish' ? '#4FAF7B' : '#EF5350';
        ctx.beginPath();
        ctx.arc(xSMC, ySMC, 4, 0, Math.PI * 2);
        ctx.fillStyle = smcColor;
        ctx.fill();

        if (showSMCLabels) {
          var smcLabelText = 'SMC ' + setup.score + '/5';
          ctx.font = 'bold 10px sans-serif';
          ctx.fillStyle = smcColor;
          ctx.textBaseline = setup.direction === 'bullish' ? 'top' : 'bottom';
          ctx.fillText(smcLabelText, xSMC + 6, setup.direction === 'bullish' ? ySMC + 4 : ySMC - 4);
        }
      });
    });

    // Breaker & Mitigation Blocks — same filled-rectangle technique as
    // FVG/Order Blocks. A Breaker's fill color reflects its NEW (flipped)
    // role; a Mitigation Block's fill color reflects its ORIGINAL
    // (unflipped) direction — both this codebase's established bullish/
    // bearish palette. Mitigation Blocks render as a fixed historical
    // span (source -> mitigation); Breakers extend like an active OB
    // until mitigated/re-invalidated.
    breakerMitigationInsts.forEach(function (inst) {
      if (!inst._lastBreakerMitigation || !window.bigCandleSeries) return;
      var bmP = inst.params;
      var showBullishBreakers = bmP.showBullishBreakers !== 'Off';
      var showBearishBreakers = bmP.showBearishBreakers !== 'Off';
      var showBullishMitigation = bmP.showBullishMitigation !== 'Off';
      var showBearishMitigation = bmP.showBearishMitigation !== 'Off';
      var showMitigatedBreakers = bmP.showMitigatedBreakers !== 'Off';
      var showHistoricalBM = bmP.showHistorical !== 'Off';
      var extendActiveBM = bmP.extendActive !== 'Off';
      var showLabelsBM = bmP.showLabels !== 'Off';
      var lastTimeBM = times.length ? times[times.length - 1] : null;

      inst._lastBreakerMitigation.breakers.forEach(function (breaker) {
        if (breaker.direction === 'BULLISH_BREAKER' && !showBullishBreakers) return;
        if (breaker.direction === 'BEARISH_BREAKER' && !showBearishBreakers) return;
        var terminalBreaker = (breaker.status === 'fully_mitigated' || breaker.status === 'invalidated');
        if (terminalBreaker && !showMitigatedBreakers) return;
        if (terminalBreaker && !showHistoricalBM) return;

        var endTimeBreaker;
        if (breaker.status === 'fully_mitigated') endTimeBreaker = _candles[breaker.mitigatedIndex].time;
        else if (breaker.status === 'invalidated') endTimeBreaker = _candles[breaker.reInvalidatedIndex].time;
        else endTimeBreaker = extendActiveBM ? lastTimeBM : breaker.invalidationTime;

        var xStartBreaker = timeScale.timeToCoordinate(breaker.invalidationTime);
        var xEndBreaker = timeScale.timeToCoordinate(endTimeBreaker);
        var yTopBreaker = window.bigCandleSeries.priceToCoordinate(breaker.top);
        var yBottomBreaker = window.bigCandleSeries.priceToCoordinate(breaker.bottom);
        if (xStartBreaker === null || xEndBreaker === null || yTopBreaker === null || yBottomBreaker === null ||
            isNaN(xStartBreaker) || isNaN(xEndBreaker) || isNaN(yTopBreaker) || isNaN(yBottomBreaker)) return;
        var xRightBreaker = Math.max(xEndBreaker, xStartBreaker + 1);

        var breakerColor = breaker.direction === 'BULLISH_BREAKER' ? '79, 175, 123' : '239, 83, 80';
        var breakerAlpha = terminalBreaker ? 0.05 : 0.14;
        ctx.fillStyle = 'rgba(' + breakerColor + ', ' + breakerAlpha + ')';
        ctx.fillRect(xStartBreaker, yTopBreaker, xRightBreaker - xStartBreaker, yBottomBreaker - yTopBreaker);

        if (showLabelsBM) {
          var breakerLabelText = breaker.direction === 'BULLISH_BREAKER' ? 'Bullish Breaker' : 'Bearish Breaker';
          ctx.font = '10px sans-serif';
          var breakerLabelWidth = ctx.measureText(breakerLabelText).width;
          if ((xRightBreaker - xStartBreaker) >= breakerLabelWidth + 6) {
            ctx.fillStyle = 'rgb(' + breakerColor + ')';
            ctx.textBaseline = 'bottom';
            ctx.fillText(breakerLabelText, xStartBreaker + 2, yTopBreaker - 2);
          }
        }
      });

      if (showHistoricalBM) {
        inst._lastBreakerMitigation.mitigations.forEach(function (mit) {
          if (mit.direction === 'BULLISH_MITIGATION' && !showBullishMitigation) return;
          if (mit.direction === 'BEARISH_MITIGATION' && !showBearishMitigation) return;

          var xStartMit = timeScale.timeToCoordinate(mit.sourceTime);
          var xEndMit = timeScale.timeToCoordinate(mit.mitigationTime);
          var yTopMit = window.bigCandleSeries.priceToCoordinate(mit.top);
          var yBottomMit = window.bigCandleSeries.priceToCoordinate(mit.bottom);
          if (xStartMit === null || xEndMit === null || yTopMit === null || yBottomMit === null ||
              isNaN(xStartMit) || isNaN(xEndMit) || isNaN(yTopMit) || isNaN(yBottomMit)) return;
          var xRightMit = Math.max(xEndMit, xStartMit + 1);

          // A visually distinct but subtle treatment: a hatched look isn't
          // available on a plain canvas fill without a pattern image, so
          // Mitigation Blocks use an even lower alpha than an active
          // Breaker/OB fill — subtle, per spec, not a new color.
          var mitColor = mit.direction === 'BULLISH_MITIGATION' ? '79, 175, 123' : '239, 83, 80';
          ctx.fillStyle = 'rgba(' + mitColor + ', 0.04)';
          ctx.fillRect(xStartMit, yTopMit, xRightMit - xStartMit, yBottomMit - yTopMit);

          if (showLabelsBM) {
            var mitLabelText = mit.direction === 'BULLISH_MITIGATION' ? 'Bullish Mitigation' : 'Bearish Mitigation';
            ctx.font = '10px sans-serif';
            var mitLabelWidth = ctx.measureText(mitLabelText).width;
            if ((xRightMit - xStartMit) >= mitLabelWidth + 6) {
              ctx.fillStyle = 'rgb(' + mitColor + ')';
              ctx.textBaseline = 'bottom';
              ctx.fillText(mitLabelText, xStartMit + 2, yBottomMit - 2);
            }
          }
        });
      }
    });

    ctx.restore();

    _drawPaneClouds();
  }

  function _drawPaneClouds() {
    if (!PaneManager || !PaneManager._panes) return;
    for (var pid in PaneManager._panes) {
      var pane = PaneManager._panes[pid];
      if (!pane || !pane.chart || !pane.wrapper) continue;

      var canvasId = 'lwc-pane-canvas-' + pid;
      var canvas = pane.wrapper.querySelector('#' + canvasId);

      var activeInst = null;
      for (var id in _instances) {
        if (_instances[id].paneId === pid && _instances[id].visible) {
          activeInst = _instances[id];
          break;
        }
      }

      if (!activeInst || (activeInst.def.id !== 'STOCH' && activeInst.def.id !== 'RSI' && activeInst.def.id !== 'WILLIAMSR' && activeInst.def.id !== 'CCI' && activeInst.def.id !== 'MFI')) {
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
        continue;
      }

      if (!canvas) {
        canvas = document.createElement('canvas');
        canvas.id = canvasId;
        canvas.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2;';
        pane.wrapper.appendChild(canvas);
      } else if (canvas.nextSibling) {
        // Ensure canvas stays on top of chartDiv
        pane.wrapper.appendChild(canvas);
      }

      var rect = pane.wrapper.getBoundingClientRect();
      var dpr = window.devicePixelRatio || 1;
      var w = Math.round(rect.width);
      var h = Math.round(rect.height);
      if (w <= 0 || h <= 0) continue;

      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = w + 'px';
        canvas.style.height = h + 'px';
      }

      var ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      var timeScale = pane.chart.timeScale();
      if (!timeScale || !_candles || !_candles.length) continue;

      var times = _candles.map(function (c) { return c.time; });

      if (activeInst.def.id === 'STOCH' && activeInst.series.k) {
        var yUpper = activeInst.series.upper ? activeInst.series.upper.priceToCoordinate(80) : activeInst.series.k.priceToCoordinate(80);
        var yLower = activeInst.series.lower ? activeInst.series.lower.priceToCoordinate(20) : activeInst.series.k.priceToCoordinate(20);
        if (yUpper === null || yLower === null || isNaN(yUpper) || isNaN(yLower)) continue;

        var rightScaleWidth = 68;
        var plotW = Math.max(0, w - rightScaleWidth);

        // 1. Channel Background Fill between 20 and 80
        var topY = Math.min(yUpper, yLower);
        var bandH = Math.abs(yLower - yUpper);
        if (bandH > 0 && plotW > 0) {
          ctx.fillStyle = 'rgba(41, 98, 255, 0.09)';
          ctx.fillRect(0, topY, plotW, bandH);
        }

        // 2. Extract series points
        var kPts = [];
        var dPts = [];
        for (var i = 0; i < times.length; i++) {
          var t = times[i];
          var valObj = (activeInst._dataByTime && activeInst._dataByTime[t]) ? activeInst._dataByTime[t] : null;
          if (!valObj) continue;
          var x = timeScale.timeToCoordinate(t);
          if (x === null || isNaN(x)) continue;

          var kVal = valObj.k;
          var dVal = valObj.d;
          var yK = (kVal !== null && isFinite(kVal)) ? activeInst.series.k.priceToCoordinate(kVal) : null;
          var yD = (dVal !== null && isFinite(dVal) && activeInst.series.d) ? activeInst.series.d.priceToCoordinate(dVal) : null;

          kPts.push({ x: x, y: yK, val: kVal, time: t, idx: i });
          if (yD !== null && !isNaN(yD)) {
            dPts.push({ x: x, y: yD, val: dVal, time: t, idx: i });
          }
        }

        // 3. Overbought Shading (%K >= 80, Green Cloud)
        var obSegments = [];
        var curOb = [];
        for (var i = 0; i < kPts.length; i++) {
          var pt = kPts[i];
          if (pt.val !== null && pt.val >= 80 && pt.y !== null && !isNaN(pt.y)) {
            curOb.push(pt);
          } else {
            if (curOb.length > 0) {
              obSegments.push(curOb);
              curOb = [];
            }
          }
        }
        if (curOb.length > 0) obSegments.push(curOb);

        obSegments.forEach(function (seg) {
          if (!seg.length) return;
          var first = seg[0];
          var last = seg[seg.length - 1];

          // Interpolate start crossing point at yUpper
          var startX = first.x;
          if (first.idx > 0 && kPts[first.idx - 1] && kPts[first.idx - 1].val !== null && kPts[first.idx - 1].y !== null) {
            var prev = kPts[first.idx - 1];
            if (prev.val < 80) {
              var frac = (80 - prev.val) / (first.val - prev.val);
              startX = prev.x + frac * (first.x - prev.x);
            }
          }

          // Interpolate end crossing point at yUpper
          var endX = last.x;
          if (last.idx < kPts.length - 1 && kPts[last.idx + 1] && kPts[last.idx + 1].val !== null && kPts[last.idx + 1].y !== null) {
            var nxt = kPts[last.idx + 1];
            if (nxt.val < 80) {
              var frac = (80 - last.val) / (nxt.val - last.val);
              endX = last.x + frac * (nxt.x - last.x);
            }
          }

          ctx.beginPath();
          ctx.moveTo(startX, yUpper);
          seg.forEach(function (p) { ctx.lineTo(p.x, p.y); });
          ctx.lineTo(endX, yUpper);
          ctx.closePath();

          var minY = Math.min.apply(null, seg.map(function(p){return p.y;}));
          var grad = ctx.createLinearGradient(0, minY, 0, yUpper);
          grad.addColorStop(0, 'rgba(79, 175, 123, 0.32)');
          grad.addColorStop(1, 'rgba(79, 175, 123, 0.06)');
          ctx.fillStyle = grad;
          ctx.fill();
        });

        // 4. Oversold Shading (%K <= 20, Red Cloud)
        var osSegments = [];
        var curOs = [];
        for (var i = 0; i < kPts.length; i++) {
          var pt = kPts[i];
          if (pt.val !== null && pt.val <= 20 && pt.y !== null && !isNaN(pt.y)) {
            curOs.push(pt);
          } else {
            if (curOs.length > 0) {
              osSegments.push(curOs);
              curOs = [];
            }
          }
        }
        if (curOs.length > 0) osSegments.push(curOs);

        osSegments.forEach(function (seg) {
          if (!seg.length) return;
          var first = seg[0];
          var last = seg[seg.length - 1];

          // Interpolate start crossing point at yLower
          var startX = first.x;
          if (first.idx > 0 && kPts[first.idx - 1] && kPts[first.idx - 1].val !== null && kPts[first.idx - 1].y !== null) {
            var prev = kPts[first.idx - 1];
            if (prev.val > 20) {
              var frac = (20 - prev.val) / (first.val - prev.val);
              startX = prev.x + frac * (first.x - prev.x);
            }
          }

          // Interpolate end crossing point at yLower
          var endX = last.x;
          if (last.idx < kPts.length - 1 && kPts[last.idx + 1] && kPts[last.idx + 1].val !== null && kPts[last.idx + 1].y !== null) {
            var nxt = kPts[last.idx + 1];
            if (nxt.val > 20) {
              var frac = (20 - last.val) / (nxt.val - last.val);
              endX = last.x + frac * (nxt.x - last.x);
            }
          }

          ctx.beginPath();
          ctx.moveTo(startX, yLower);
          seg.forEach(function (p) { ctx.lineTo(p.x, p.y); });
          ctx.lineTo(endX, yLower);
          ctx.closePath();

          var maxY = Math.max.apply(null, seg.map(function(p){return p.y;}));
          var grad = ctx.createLinearGradient(0, yLower, 0, maxY);
          grad.addColorStop(0, 'rgba(239, 83, 80, 0.06)');
          grad.addColorStop(1, 'rgba(239, 83, 80, 0.32)');
          ctx.fillStyle = grad;
          ctx.fill();
        });

        // 5. Highlight circles at crossover or turning points in OB / OS zone
        for (var i = 1; i < kPts.length; i++) {
          var pt = kPts[i];
          var prev = kPts[i - 1];
          if ((pt.val >= 80 || pt.val <= 20) && pt.y !== null && !isNaN(pt.y)) {
            var isExtremum = false;
            if (i > 1 && i < kPts.length - 1) {
              var nxt = kPts[i + 1];
              if (pt.val >= 80 && pt.val >= prev.val && pt.val >= nxt.val) isExtremum = true;
              if (pt.val <= 20 && pt.val <= prev.val && pt.val <= nxt.val) isExtremum = true;
            }
            if (isExtremum) {
              ctx.beginPath();
              ctx.arc(pt.x, pt.y, 3, 0, 2 * Math.PI);
              ctx.fillStyle = '#2A75FF';
              ctx.fill();
              ctx.strokeStyle = '#fff';
              ctx.lineWidth = 1.5;
              ctx.stroke();
            }
          }
        }
      }

      // RSI Channel Shading (30 to 70)
      if (activeInst.def.id === 'RSI' && activeInst.series.rsi && activeInst.series.upper && activeInst.series.lower) {
        var yUpper = activeInst.series.upper.priceToCoordinate(Number(activeInst.params.upper || 70));
        var yLower = activeInst.series.lower.priceToCoordinate(Number(activeInst.params.lower || 30));
        if (yUpper === null || yLower === null || isNaN(yUpper) || isNaN(yLower)) continue;

        var rightScaleWidth = 68;
        var plotW = Math.max(0, w - rightScaleWidth);

        var topY = Math.min(yUpper, yLower);
        var bandH = Math.abs(yLower - yUpper);
        if (bandH > 0 && plotW > 0) {
          ctx.fillStyle = 'rgba(179, 136, 255, 0.08)';
          ctx.fillRect(0, topY, plotW, bandH);
        }
      }

      // CCI: a static blue band between the +100/-100 reference levels,
      // matching TradingView's CCI channel highlight.
      if (activeInst.def.id === 'CCI' && activeInst.series.upper && activeInst.series.lower) {
        var yUpperCci = activeInst.series.upper.priceToCoordinate(100);
        var yLowerCci = activeInst.series.lower.priceToCoordinate(-100);
        if (yUpperCci === null || yLowerCci === null || isNaN(yUpperCci) || isNaN(yLowerCci)) continue;

        var rightScaleWidth = 68;
        var plotW = Math.max(0, w - rightScaleWidth);

        var topY = Math.min(yUpperCci, yLowerCci);
        var bandH = Math.abs(yLowerCci - yUpperCci);
        if (bandH > 0 && plotW > 0) {
          ctx.fillStyle = 'rgba(41, 98, 255, 0.08)';
          ctx.fillRect(0, topY, plotW, bandH);
        }
      }

      // MFI: a static band between the 80/20 reference levels, tinted with
      // the instance's OWN line color (tracks a custom color, unlike CCI's
      // fixed blue) so the highlight always matches whatever's actually drawn.
      if (activeInst.def.id === 'MFI' && activeInst.series.ob && activeInst.series.os) {
        var yObMfi = activeInst.series.ob.priceToCoordinate(80);
        var yOsMfi = activeInst.series.os.priceToCoordinate(20);
        if (yObMfi === null || yOsMfi === null || isNaN(yObMfi) || isNaN(yOsMfi)) continue;

        var rightScaleWidth = 68;
        var plotW = Math.max(0, w - rightScaleWidth);

        var topY = Math.min(yObMfi, yOsMfi);
        var bandH = Math.abs(yOsMfi - yObMfi);
        if (bandH > 0 && plotW > 0) {
          ctx.fillStyle = _hexToRgba(activeInst.color || '#B388FF', 0.08);
          ctx.fillRect(0, topY, plotW, bandH);
        }
      }

      // Williams %R: a static purple band between the -20/-80 reference
      // levels — NOT a fill hugging the WR line's own shape. It sits both
      // above and below the line wherever the line crosses through it.
      if (activeInst.def.id === 'WILLIAMSR' && activeInst.series.wr && activeInst.series.ob && activeInst.series.os) {
        var yOb = activeInst.series.ob.priceToCoordinate(-20);
        var yOs = activeInst.series.os.priceToCoordinate(-80);
        if (yOb === null || yOs === null || isNaN(yOb) || isNaN(yOs)) continue;

        var rightScaleWidth = 68;
        var plotW = Math.max(0, w - rightScaleWidth);

        var topY = Math.min(yOb, yOs);
        var bandH = Math.abs(yOs - yOb);
        if (bandH > 0 && plotW > 0) {
          ctx.fillStyle = 'rgba(133, 94, 201, 0.14)';
          ctx.fillRect(0, topY, plotW, bandH);
        }
      }
    }
  }

  var _drawBbCloud = _drawOverlayClouds;

  var _candleByTime = {};
  var _sharedCrosshairEl = null;
  var _sharedCrosshairDateEl = null;

  function _formatTimeBadge(t) {
    if (t === null || t === undefined) return '';
    var realT = t;
    if (typeof t === 'number') {
      if (window._intradayBarTimes && window._intradayBarSecs) {
        var bi = Math.round(t / window._intradayBarSecs);
        if (bi >= 0 && bi < window._intradayBarTimes.length) {
          realT = window._intradayBarTimes[bi];
        }
      }
    }
    if (typeof realT === 'object' && realT.year && realT.month && realT.day) {
      var d = new Date(Date.UTC(realT.year, realT.month - 1, realT.day));
      return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
    }
    if (typeof realT === 'string') {
      var parts = realT.split('-');
      if (parts.length === 3) {
        var d = new Date(Date.UTC(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10)));
        return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
      }
      return realT;
    }
    if (typeof realT === 'number') {
      var ms = realT > 1e11 ? realT : realT * 1000;
      var d = new Date(ms);
      var isIntraday = (d.getUTCHours() !== 0 || d.getUTCMinutes() !== 0);
      if (isIntraday) {
        var timeStr = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
        var dateStr = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' });
        return dateStr + ' ' + timeStr;
      } else {
        return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' });
      }
    }
    return String(realT);
  }

  function _ensureSharedCrosshair() {
    var centerArea = document.querySelector('.center-area');
    if (!centerArea) return null;
    if (!_sharedCrosshairEl || !_sharedCrosshairEl.parentNode) {
      _sharedCrosshairEl = document.createElement('div');
      _sharedCrosshairEl.id = 'lwc-shared-crosshair-line';
      _sharedCrosshairEl.style.cssText = [
        'position:absolute',
        'top:0',
        'bottom:0',
        'width:1px',
        'background:repeating-linear-gradient(to bottom, #758696 0px, #758696 4px, transparent 4px, transparent 8px)',
        'pointer-events:none',
        'z-index:25',
        'display:none'
      ].join(';');

      _sharedCrosshairDateEl = document.createElement('div');
      _sharedCrosshairDateEl.id = 'lwc-shared-crosshair-date';
      _sharedCrosshairDateEl.style.cssText = [
        'position:absolute',
        'bottom:2px',
        'left:50%',
        'transform:translateX(-50%)',
        'background:#2a2e39',
        'color:#d1d4dc',
        'font-size:10px',
        'font-family:monospace',
        'padding:2px 6px',
        'border-radius:2px',
        'white-space:nowrap',
        'pointer-events:none',
        'border:1px solid #363c4e',
        'box-shadow:0 2px 4px rgba(0,0,0,0.5)',
        'z-index:26'
      ].join(';');
      _sharedCrosshairEl.appendChild(_sharedCrosshairDateEl);
      centerArea.appendChild(_sharedCrosshairEl);

      if (!centerArea._crosshairLeaveBound) {
        centerArea.addEventListener('mouseleave', function () {
          CrosshairSyncManager.sync(null, null);
        });
        centerArea._crosshairLeaveBound = true;
      }
    }
    return _sharedCrosshairEl;
  }

  var CrosshairSyncManager = {
    _isSyncing: false,

    sync: function (sourceChart, param) {
      if (this._isSyncing) return;
      this._isSyncing = true;
      try {
        if (!param || param.time === undefined || !param.point) {
          // Hide shared single vertical line overlay
          if (_sharedCrosshairEl) _sharedCrosshairEl.style.display = 'none';
          // Clear crosshairs on all charts
          if (window.bigChart && window.bigChart !== sourceChart) {
            try { window.bigChart.clearCrosshairPosition(); } catch (e) {}
          }
          for (var pid in PaneManager._panes) {
            var p = PaneManager._panes[pid];
            if (p && p.chart && p.chart !== sourceChart) {
              try { p.chart.clearCrosshairPosition(); } catch (e) {}
            }
          }
          return;
        }

        var t = param.time;

        var hasPanes = Object.keys(PaneManager._panes).length > 0;
        if (hasPanes) {
          var overlay = _ensureSharedCrosshair();
          var x = null;
          if (param.point && typeof param.point.x === 'number' && !isNaN(param.point.x) && param.point.x >= 0) {
            x = param.point.x;
          } else if (sourceChart && typeof sourceChart.timeScale === 'function' && t !== undefined) {
            try { x = sourceChart.timeScale().timeToCoordinate(t); } catch (e) {}
          } else if (window.bigChart && typeof window.bigChart.timeScale === 'function' && t !== undefined) {
            try { x = window.bigChart.timeScale().timeToCoordinate(t); } catch (e) {}
          }

          if (overlay && x !== null && !isNaN(x) && x >= 0) {
            overlay.style.left = x + 'px';
            overlay.style.display = 'block';
            if (_sharedCrosshairDateEl) {
              _sharedCrosshairDateEl.textContent = _formatTimeBadge(t);
            }
          }
        }

        // 1. If source is an indicator pane, clear main chart programmatic crosshair (so no phantom horizontal line)
        if (window.bigChart && window.bigChart !== sourceChart) {
          try { window.bigChart.clearCrosshairPosition(); } catch (e) {}
        }

        // 2. Update HUD values and clear non-active indicator panes
        for (var instId in _instances) {
          var inst = _instances[instId];
          if (!inst.visible || !inst.paneId) continue;
          var pane = PaneManager.get(inst.paneId);
          if (!pane) continue;
          if (pane.chart && pane.chart !== sourceChart) {
            try { pane.chart.clearCrosshairPosition(); } catch (e) {}
          }

          var valObj = (inst._dataByTime && inst._dataByTime[t]) ? inst._dataByTime[t] : null;

          if (inst.def.id === 'MACD') {
            if (valObj) {
              var hVal = valObj.histogram;
              var sVal = valObj.signal;
              var hCol = (hVal !== null && hVal >= 0) ? '#26a69a' : '#ef5350';
              var html = (hVal !== null && isFinite(hVal) ? '<span style="color:' + hCol + ';margin-right:6px;">' + (hVal >= 0 ? '+' : '') + Number(hVal).toFixed(2) + '</span>' : '') +
                         (valObj.macd !== null && isFinite(valObj.macd) ? '<span style="color:#2962FF;margin-right:6px;">' + Number(valObj.macd).toFixed(2) + '</span>' : '') +
                         (sVal !== null && isFinite(sVal) ? '<span style="color:#FF6D00;">' + Number(sVal).toFixed(2) + '</span>' : '');
              PaneManager.updateLegend(inst.paneId, html);
            }
          } else if (inst.def.id === 'RSI') {
            if (valObj && valObj.rsi !== null && isFinite(valObj.rsi)) {
              PaneManager.updateLegend(inst.paneId, '<span style="color:#B388FF;font-weight:600;">' + Number(valObj.rsi).toFixed(2) + '</span>');
            }
          } else if (inst.def.id === 'STOCH') {
            if (valObj) {
              var kVal = valObj.k;
              var dVal = valObj.d;
              var html = (kVal !== null && kVal !== undefined && isFinite(kVal) ? '<span style="color:#2A75FF;font-weight:600;margin-right:6px;">' + Number(kVal).toFixed(2) + '</span>' : '') +
                         (dVal !== null && dVal !== undefined && isFinite(dVal) ? '<span style="color:#FF9800;font-weight:600;">' + Number(dVal).toFixed(2) + '</span>' : '');
              PaneManager.updateLegend(inst.paneId, html);
            }
          } else if (inst.def.id === 'ATR') {
            if (valObj && valObj.atr !== null && valObj.atr !== undefined && isFinite(valObj.atr)) {
              PaneManager.updateLegend(inst.paneId, '<span style="color:#5C9CE6;font-weight:600;">' + Number(valObj.atr).toFixed(2) + '</span>');
            }
          } else if (inst.def.id === 'DMI') {
            if (valObj) {
              var aVal = valObj.adx;
              var pVal = valObj.plusDi;
              var mVal = valObj.minusDi;
              var html = (aVal !== null && aVal !== undefined && isFinite(aVal) ? '<span style="color:#E91E63;font-weight:600;margin-right:6px;">' + Number(aVal).toFixed(4) + '</span>' : '') +
                         (pVal !== null && pVal !== undefined && isFinite(pVal) ? '<span style="color:#2196F3;font-weight:600;margin-right:6px;">' + Number(pVal).toFixed(4) + '</span>' : '') +
                         (mVal !== null && mVal !== undefined && isFinite(mVal) ? '<span style="color:#FF9800;font-weight:600;">' + Number(mVal).toFixed(4) + '</span>' : '');
              PaneManager.updateLegend(inst.paneId, html);
            }
          } else if (inst.def.id === 'ADX') {
            if (valObj && valObj.adx !== null && valObj.adx !== undefined && isFinite(valObj.adx)) {
              PaneManager.updateLegend(inst.paneId, '<span style="color:#E91E63;font-weight:600;">' + Number(valObj.adx).toFixed(2) + '</span>');
            }
          } else if (inst.def.id === 'OBV') {
            if (valObj && valObj.obv !== null && valObj.obv !== undefined && isFinite(valObj.obv)) {
              PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#2962FF') + ';font-weight:600;">' + Number(valObj.obv).toLocaleString('en-US') + '</span>');
            }
          } else if (inst.def.id === 'CCI') {
            if (valObj) {
              var cciVal = valObj.cci;
              var cciSmoothVal = valObj.cciSmooth;
              var cciHtml = (cciVal !== null && cciVal !== undefined && isFinite(cciVal) ? '<span style="color:' + (inst.color || '#2A75FF') + ';font-weight:600;margin-right:6px;">' + Number(cciVal).toFixed(2) + '</span>' : '') +
                            (cciSmoothVal !== null && cciSmoothVal !== undefined && isFinite(cciSmoothVal) ? '<span style="color:#FFCA28;font-weight:600;">' + Number(cciSmoothVal).toFixed(2) + '</span>' : '');
              PaneManager.updateLegend(inst.paneId, cciHtml);
            }
          } else if (inst.def.id === 'WILLIAMSR') {
            if (valObj && valObj.wr !== null && valObj.wr !== undefined && isFinite(valObj.wr)) {
              PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#855EC9') + ';font-weight:600;">' + Number(valObj.wr).toFixed(2) + '</span>');
            }
          } else if (inst.def.id === 'MFI') {
            if (valObj && valObj.mfi !== null && valObj.mfi !== undefined && isFinite(valObj.mfi)) {
              PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#B388FF') + ';font-weight:600;">' + Number(valObj.mfi).toFixed(2) + '</span>');
            }
          } else if (inst.def.id === 'ROC') {
            if (valObj) {
              var rocVal = valObj.roc;
              var rocSigVal = valObj.signal;
              var rocHtml = (rocVal !== null && rocVal !== undefined && isFinite(rocVal) ? '<span style="color:' + (inst.color || '#2962FF') + ';font-weight:600;margin-right:6px;">' + Number(rocVal).toFixed(2) + '</span>' : '') +
                            (rocSigVal !== null && rocSigVal !== undefined && isFinite(rocSigVal) ? '<span style="color:#FF9800;font-weight:600;">' + Number(rocSigVal).toFixed(2) + '</span>' : '');
              PaneManager.updateLegend(inst.paneId, rocHtml);
            }
          } else if (inst.def.id === 'AROON') {
            if (valObj) {
              var aroonUpVal = valObj.up;
              var aroonDownVal = valObj.down;
              var aroonHtml = (aroonUpVal !== null && aroonUpVal !== undefined && isFinite(aroonUpVal) ? '<span style="color:#FF9800;font-weight:600;margin-right:6px;">' + Number(aroonUpVal).toFixed(2) + '</span>' : '') +
                              (aroonDownVal !== null && aroonDownVal !== undefined && isFinite(aroonDownVal) ? '<span style="color:#2962FF;font-weight:600;">' + Number(aroonDownVal).toFixed(2) + '</span>' : '');
              PaneManager.updateLegend(inst.paneId, aroonHtml);
            }
          } else if (inst.def.id === 'CMF') {
            if (valObj && valObj.cmf !== null && valObj.cmf !== undefined && isFinite(valObj.cmf)) {
              PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#26A69A') + ';font-weight:600;">' + Number(valObj.cmf).toFixed(2) + '</span>');
            }
          }
        }
      } catch (err) {
      } finally {
        this._isSyncing = false;
      }
    }
  };

  function _attachMainCrosshairSync() {
    _attachMainChartSync();
  }

  function _attachMainChartSync() {
    if (!window.bigChart) return;
    if (window.bigChart._crosshairSyncHandler) {
      try { window.bigChart.unsubscribeCrosshairMove(window.bigChart._crosshairSyncHandler); } catch (e) {}
    }
    var handler = function (param) {
      CrosshairSyncManager.sync(window.bigChart, param);
    };
    window.bigChart.subscribeCrosshairMove(handler);
    window.bigChart._crosshairSyncHandler = handler;

    if (!window.bigChart._bbRangeSyncAttached) {
      window.bigChart.timeScale().subscribeVisibleLogicalRangeChange(function (lr) {
        _broadcastLogicalRange(window.bigChart, lr);
        _keepCloudLoopAlive(500);
      });
      window.bigChart.timeScale().subscribeVisibleTimeRangeChange(function () {
        _keepCloudLoopAlive(500);
      });
      window.bigChart._bbRangeSyncAttached = true;
    }

    var chartContainer = document.getElementById('chart-container');
    if (chartContainer && !chartContainer._bbEventsBound) {
      // A price-axis (Y-axis) drag-to-rescale has no dedicated LWC
      // subscription (unlike the time scale above), and two things about it
      // defeated a plain "redraw on this DOM event" listener:
      //   1. LWC may keep the actual scale settling (inertia/easing) for a
      //      bit after the pointer stops moving, so redrawing only on the
      //      DOM event itself still leaves the canvas fill trailing behind
      //      the still-animating native price scale — the "gap that takes
      //      about a second to catch up" this is fixing.
      //   2. LWC's own price-scale hit-handling may stop the event from
      //      bubbling to this container at all, in which case a bubble-
      //      phase listener never fires during the drag in the first place.
      // _keepCloudLoopAlive addresses (1) by redrawing every frame for a
      // short window after the last qualifying event rather than once;
      // capture:true on every listener plus a mousedown/pointerdown trigger
      // addresses (2) by running before any deeper stopPropagation() can
      // block it, and by starting the window on press rather than waiting
      // for a move that might never bubble.
      var onDrag = function () {
        _keepCloudLoopAlive(800);
      };
      chartContainer.addEventListener('pointerdown', onDrag, { passive: true, capture: true });
      chartContainer.addEventListener('mousedown', onDrag, { passive: true, capture: true });
      chartContainer.addEventListener('pointermove', function (e) {
        if (e.buttons > 0) onDrag();
      }, { passive: true, capture: true });
      chartContainer.addEventListener('mousemove', function (e) {
        if (e.buttons > 0) onDrag();
      }, { passive: true, capture: true });
      chartContainer.addEventListener('touchmove', onDrag, { passive: true, capture: true });
      chartContainer.addEventListener('wheel', onDrag, { passive: true, capture: true });
      chartContainer._bbEventsBound = true;
    }
  }

  // Redraws the overlay-cloud canvas every frame for `durationMs` after the
  // most recent call (repeated calls keep extending the window, they don't
  // stack extra loops) — cheap while genuinely idle (no loop runs at all),
  // and guarantees the canvas tracks the native chart through any trailing
  // native animation/inertia a single discrete redraw would miss.
  var _cloudLoopActive = false;
  var _cloudLoopUntil = 0;
  function _keepCloudLoopAlive(durationMs) {
    _cloudLoopUntil = Date.now() + (durationMs || 500);
    if (_cloudLoopActive) return;
    _cloudLoopActive = true;
    var step = function () {
      _drawBbCloud();
      if (Date.now() < _cloudLoopUntil) {
        requestAnimationFrame(step);
      } else {
        _cloudLoopActive = false;
      }
    };
    requestAnimationFrame(step);
  }

  function _syncMainChartLayout() {
    var cp = document.getElementById('chart-container');
    if (window.bigChart && cp) {
      var w = cp.clientWidth;
      var h = cp.clientHeight;
      if (w > 0 && h > 0) {
        try { window.bigChart.applyOptions({ width: w, height: h }); } catch (e) {}
      }
    }
    if (window.toolManager && typeof window.toolManager.resizeCanvas === 'function') {
      try { window.toolManager.resizeCanvas(); } catch (e) {}
    }
    _drawBbCloud();
  }

  var PaneManager = {
    _panes: {},

    getOrCreate: function (paneId, titleText) {
      if (this._panes[paneId]) return this._panes[paneId];

      var centerArea = document.querySelector('.center-area');
      if (!centerArea) return null;

      var wrapper = document.createElement('div');
      wrapper.id = 'lwc-pane-' + paneId;
      wrapper.className = 'lwc-indicator-pane';
      wrapper.style.cssText = [
        'height:140px',
        'position:relative',
        'border-top:1px solid #2a2e39',
        'background:#0A0A0B',
        'flex-shrink:0',
        'min-height:70px',
        'max-height:80vh'
      ].join(';');

      // ── Splitter / Resize Drag Bar between Main Chart and Indicator Pane ──
      var splitter = document.createElement('div');
      splitter.className = 'lwc-pane-splitter';
      splitter.title = 'Drag to resize pane (double-click to reset)';
      splitter.style.cssText = [
        'position:absolute',
        'top:-5px',
        'left:0',
        'width:100%',
        'height:10px',
        'cursor:row-resize',
        'z-index:30',
        'display:flex',
        'align-items:center',
        'justify-content:center',
        'background:transparent'
      ].join(';');

      var splitterLine = document.createElement('div');
      splitterLine.className = 'lwc-pane-splitter-line';
      splitterLine.style.cssText = [
        'width:100%',
        'height:2px',
        'background:#2a2e39',
        'pointer-events:none',
        'transition:background 0.15s ease, height 0.15s ease'
      ].join(';');
      splitter.appendChild(splitterLine);
      wrapper.appendChild(splitter);

      var titleEl = document.createElement('div');
      titleEl.style.cssText = 'position:absolute;top:6px;left:10px;z-index:5;font-size:11px;color:#787b86;pointer-events:none;font-family:monospace;letter-spacing:0.3px;display:flex;align-items:center;gap:4px;user-select:none;';
      titleEl.innerHTML = '<span style="color:#787b86;font-weight:600;">' + titleText + '</span>';
      wrapper.appendChild(titleEl);

      var chartDiv = document.createElement('div');
      chartDiv.style.cssText = 'width:100%;height:100%;';
      wrapper.appendChild(chartDiv);

      // Insert before openPositionsPanel, after chart-container
      var posPanel = document.getElementById('openPositionsPanel');
      if (posPanel && posPanel.parentElement === centerArea) {
        centerArea.insertBefore(wrapper, posPanel);
      } else {
        centerArea.appendChild(wrapper);
      }

      var w = chartDiv.clientWidth || (document.getElementById('chart-container') || {}).clientWidth || 600;
      var LWC = window.LightweightCharts;
      if (!LWC) return null;

      var mainTsOpts = (window.bigChart && typeof window.bigChart.timeScale === 'function') ? window.bigChart.timeScale().options() : {};
      var chart = LWC.createChart(chartDiv, {
        width: w, height: 140,
        layout: { background: { type: 'solid', color: '#0A0A0B' }, textColor: '#787b86', fontSize: 10 },
        grid: { vertLines: { color: 'rgba(42,46,57,0.25)' }, horzLines: { color: 'rgba(42,46,57,0.25)' } },
        crosshair: {
          mode: LWC.CrosshairMode.Normal,
          vertLine: { visible: false, labelVisible: false },
          horzLine: { visible: true, labelVisible: true }
        },
        rightPriceScale: { visible: true, borderColor: '#2a2e39', scaleMargins: { top: 0.12, bottom: 0.1 }, minimumWidth: 68 },
        timeScale: Object.assign({}, mainTsOpts, {
          visible: true,
          borderColor: '#2a2e39',
          timeVisible: !!window._seqMode,
          tickMarkFormatter: window.customTickMarkFormatter
        }),
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false }
      });

      var pane = { chart: chart, wrapper: wrapper, titleEl: titleEl, paneId: paneId, baseTitle: titleText, _paneSync: null };
      this._panes[paneId] = pane;

      // Splitter Hover & Drag Interactions
      var isDragging = false;
      var startY = 0;
      var startHeight = 0;

      splitter.addEventListener('mouseenter', function () {
        if (!isDragging) {
          splitterLine.style.background = '#4A90E2';
          splitterLine.style.height = '3px';
        }
      });
      splitter.addEventListener('mouseleave', function () {
        if (!isDragging) {
          splitterLine.style.background = '#2a2e39';
          splitterLine.style.height = '2px';
        }
      });

      splitter.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        e.stopPropagation();
        isDragging = true;
        startY = e.clientY;
        startHeight = wrapper.offsetHeight;
        splitterLine.style.background = '#2962FF';
        splitterLine.style.height = '3px';
        document.body.style.cursor = 'row-resize';
        document.body.style.userSelect = 'none';
        try { splitter.setPointerCapture(e.pointerId); } catch (err) {}
      });

      splitter.addEventListener('pointermove', function (e) {
        if (!isDragging) return;
        var delta = e.clientY - startY;
        var centerH = centerArea.clientHeight || 600;
        var maxH = Math.max(160, Math.floor(centerH * 0.7));
        var newH = Math.max(60, Math.min(maxH, startHeight - delta));
        wrapper.style.height = newH + 'px';
        var cw = chartDiv.clientWidth || wrapper.clientWidth || 600;
        chart.applyOptions({ width: cw, height: newH });
        _syncMainChartLayout();
      });

      var onEndDrag = function (e) {
        if (!isDragging) return;
        isDragging = false;
        splitterLine.style.background = '#2a2e39';
        splitterLine.style.height = '2px';
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        try { splitter.releasePointerCapture(e.pointerId); } catch (err) {}
        var cw = chartDiv.clientWidth || wrapper.clientWidth || 600;
        chart.applyOptions({ width: cw, height: wrapper.offsetHeight });
        _syncMainChartLayout();
      };
      splitter.addEventListener('pointerup', onEndDrag);
      splitter.addEventListener('pointercancel', onEndDrag);

      // Double-click to reset to default 140px
      splitter.addEventListener('dblclick', function (e) {
        e.preventDefault();
        wrapper.style.height = '140px';
        var cw = chartDiv.clientWidth || wrapper.clientWidth || 600;
        chart.applyOptions({ width: cw, height: 140 });
        _syncMainChartLayout();
      });

      // Bidirectional pan/scroll/zoom synchronization with main chart & other panes
      var _paneSync = function (lr) {
        _broadcastLogicalRange(chart, lr);
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(_paneSync);
      pane._paneSync = _paneSync;

      if (window.bigChart && !window.bigChart._paneSyncAttached) {
        window.bigChart.timeScale().subscribeVisibleLogicalRangeChange(function (lr) {
          _broadcastLogicalRange(window.bigChart, lr);
        });
        window.bigChart._paneSyncAttached = true;
      }

      // Native Crosshair Synchronization
      _attachMainCrosshairSync();
      var _paneCrosshairHandler = function (param) {
        CrosshairSyncManager.sync(chart, param);
      };
      chart.subscribeCrosshairMove(_paneCrosshairHandler);
      pane._paneCrosshairHandler = _paneCrosshairHandler;

      // Resize observer for responsive width and height
      if (window.ResizeObserver) {
        new ResizeObserver(function () {
          var cw = chartDiv.clientWidth || 600;
          chart.applyOptions({ width: cw, height: wrapper.offsetHeight });
          _syncMainChartLayout();
        }).observe(wrapper);
      }

      this.updateTimeScales();
      _syncMainChartLayout();
      return pane;
    },

    remove: function (paneId) {
      var pane = this._panes[paneId];
      if (!pane) return;
      if (pane._paneSync) {
        try { pane.chart.timeScale().unsubscribeVisibleLogicalRangeChange(pane._paneSync); } catch (e) {}
      }
      if (pane._paneCrosshairHandler) {
        try { pane.chart.unsubscribeCrosshairMove(pane._paneCrosshairHandler); } catch (e) {}
      }
      try { pane.chart.remove(); } catch (e) {}
      if (pane.wrapper.parentNode) pane.wrapper.parentNode.removeChild(pane.wrapper);
      delete this._panes[paneId];
      this.updateTimeScales();
      _syncMainChartLayout();
    },

    get: function (paneId) { return this._panes[paneId] || null; },

    setTitle: function (paneId, title) {
      var p = this._panes[paneId];
      if (p) {
        p.baseTitle = title;
        p.titleEl.innerHTML = '<span style="color:#787b86;font-weight:600;">' + title + '</span>';
      }
    },

    updateLegend: function (paneId, valuesHtml) {
      var p = this._panes[paneId];
      if (p && p.titleEl) {
        p.titleEl.innerHTML = '<span style="color:#787b86;font-weight:600;">' + (p.baseTitle || paneId) + '</span>' +
          (valuesHtml ? '<span style="margin-left:8px;font-family:monospace;font-size:11px;">' + valuesHtml + '</span>' : '');
      }
    },

    updateTimeScales: function () {
      var paneKeys = Object.keys(this._panes);
      var mainTs = (window.bigChart && typeof window.bigChart.timeScale === 'function') ? window.bigChart.timeScale() : null;
      var mainTsOpts = mainTs ? mainTs.options() : {};
      var curLr = mainTs ? mainTs.getVisibleLogicalRange() : null;

      if (paneKeys.length === 0) {
        if (window.bigChart) {
          try {
            window.bigChart.applyOptions({
              crosshair: {
                vertLine: { visible: true, labelVisible: true },
                horzLine: { visible: true, labelVisible: true }
              },
              rightPriceScale: { minimumWidth: 68 },
              timeScale: Object.assign({}, mainTsOpts, {
                visible: true,
                borderColor: '#2a2e39',
                timeVisible: !!window._seqMode,
                tickMarkFormatter: window.customTickMarkFormatter
              })
            });
          } catch (e) {}
        }
        if (_sharedCrosshairEl) _sharedCrosshairEl.style.display = 'none';
        return;
      }
      // When indicator pane is open: hide middle X-axis and native chart canvas vertLine,
      // using the single shared continuous vertical crosshair overlay across all panes
      if (window.bigChart) {
        try {
          window.bigChart.applyOptions({
            crosshair: {
              vertLine: { visible: false, labelVisible: false },
              horzLine: { visible: true, labelVisible: true }
            },
            rightPriceScale: { minimumWidth: 68 },
            timeScale: { visible: false }
          });
        } catch (e) {}
      }
      var lastKey = paneKeys[paneKeys.length - 1];
      paneKeys.forEach(function (pid) {
        var isBottom = (pid === lastKey);
        var p = PaneManager._panes[pid];
        if (p && p.chart) {
          try {
            p.chart.applyOptions({
              crosshair: {
                vertLine: { visible: false, labelVisible: false },
                horzLine: { visible: true, labelVisible: true }
              },
              rightPriceScale: {
                minimumWidth: 68
              },
              timeScale: Object.assign({}, mainTsOpts, {
                visible: isBottom,
                borderColor: '#2a2e39',
                timeVisible: !!window._seqMode,
                tickMarkFormatter: window.customTickMarkFormatter
              })
            });
            if (curLr) {
              p.chart.timeScale().setVisibleLogicalRange({ from: curLr.from, to: curLr.to });
            }
          } catch (e) {}
        }
      });

      requestAnimationFrame(function () {
        if (window.bigChart) {
          var lr = window.bigChart.timeScale().getVisibleLogicalRange();
          if (lr) {
            for (var pid in PaneManager._panes) {
              var p = PaneManager._panes[pid];
              if (p && p.chart) {
                try { p.chart.timeScale().setVisibleLogicalRange({ from: lr.from, to: lr.to }); } catch (e) {}
              }
            }
          }
        }
        _drawOverlayClouds();
      });
    },

    syncRange: function () {
      if (!window.bigChart) return;
      var lr = window.bigChart.timeScale().getVisibleLogicalRange();
      if (!lr) return;
      for (var pid in this._panes) {
        try { this._panes[pid].chart.timeScale().setVisibleLogicalRange({ from: lr.from, to: lr.to }); } catch (e) {}
      }
    },

    _onCrosshair: function (paneId, param) {
      // Find matching indicator instance
      for (var id in _instances) {
        var inst = _instances[id];
        if (inst.paneId === paneId) {
          if (inst.def.id === 'MACD') {
            var mData = param.seriesData.get(inst.series.macd);
            var sData = param.seriesData.get(inst.series.signal);
            var hData = param.seriesData.get(inst.series.histogram);
            var mVal = mData ? mData.value : null;
            var sVal = sData ? sData.value : null;
            var hVal = hData ? hData.value : null;
            if (mVal !== null || sVal !== null || hVal !== null) {
              var hCol = (hVal !== null && hVal >= 0) ? '#26a69a' : '#ef5350';
              var html = (hVal !== null ? '<span style="color:' + hCol + ';margin-right:6px;">' + (hVal >= 0 ? '+' : '') + hVal.toFixed(2) + '</span>' : '') +
                         (mVal !== null ? '<span style="color:#2962FF;margin-right:6px;">' + mVal.toFixed(2) + '</span>' : '') +
                         (sVal !== null ? '<span style="color:#FF6D00;">' + sVal.toFixed(2) + '</span>' : '');
              PaneManager.updateLegend(paneId, html);
            }
          } else if (inst.def.id === 'RSI') {
            var rData = param.seriesData.get(inst.series.rsi);
            if (rData && rData.value !== undefined) {
              PaneManager.updateLegend(paneId, '<span style="color:#B388FF;font-weight:600;">' + rData.value.toFixed(2) + '</span>');
            }
          }
        }
      }
    }
  };

  // ═══════════════════════════════════════════════════
  // SERIES HELPERS
  // ═══════════════════════════════════════════════════

  function _seriesData(times, values) {
    var out = [];
    var lastTime = null;
    for (var i = 0; i < times.length; i++) {
      var t = times[i];
      var v = values[i];
      if (t !== null && t !== undefined && v !== null && v !== undefined && isFinite(v)) {
        // Enforce strictly ascending time series for LightweightCharts
        if (lastTime !== null) {
          if (typeof t === 'number' && typeof lastTime === 'number' && t <= lastTime) continue;
          if (typeof t === 'string' && typeof lastTime === 'string' && t <= lastTime) continue;
        }
        out.push({ time: t, value: Number(v) });
        lastTime = t;
      }
    }
    return out;
  }

  // Like _seriesData, but for point sets that don't share the main candle
  // time array (e.g. Ichimoku's displaced/projected Span A/B and Chikou
  // points, whose display times can precede or follow the candle dataset).
  function _seriesDataFromPoints(pts) {
    var out = [];
    var lastTime = null;
    for (var i = 0; i < pts.length; i++) {
      var t = pts[i].time, v = pts[i].value;
      if (t === null || t === undefined || v === null || v === undefined || !isFinite(v)) continue;
      if (lastTime !== null && t <= lastTime) continue;
      out.push({ time: t, value: Number(v) });
      lastTime = t;
    }
    return out;
  }

  /** '#RRGGBB' -> 'rgba(r,g,b,alpha)'. Falls back to a neutral gray for
   *  anything not in that exact form (e.g. an already-rgba color). */
  function _hexToRgba(hex, alpha) {
    if (typeof hex !== 'string' || hex.charAt(0) !== '#' || hex.length !== 7) {
      return 'rgba(120,123,134,' + alpha + ')';
    }
    var r = parseInt(hex.substr(1, 2), 16);
    var g = parseInt(hex.substr(3, 2), 16);
    var b = parseInt(hex.substr(5, 2), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return 'rgba(120,123,134,' + alpha + ')';
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  function _lineOpts(color, width, style, visible) {
    var LWC = window.LightweightCharts;
    return {
      color: color,
      lineWidth: width || 1.5,
      lineStyle: style !== undefined ? style : (LWC ? LWC.LineStyle.Solid : 0),
      priceLineVisible: false,
      lastValueVisible: visible !== false,
      crosshairMarkerVisible: false
    };
  }

  // ═══════════════════════════════════════════════════
  // SERIES FACTORY
  // ═══════════════════════════════════════════════════

  function _createSeries(inst) {
    var def = inst.def;
    var LWC = window.LightweightCharts;
    if (!LWC) return;

    // Reset series dictionary
    inst.series = {};

    switch (def.id) {
      case 'SMA': {
        if (!window.bigChart) break;
        inst.series.main = window.bigChart.addLineSeries(_lineOpts(inst.color, 1.5));
        break;
      }
      case 'EMA': {
        if (!window.bigChart) break;
        inst.series.main = window.bigChart.addLineSeries(_lineOpts(inst.color, 1.5));
        break;
      }
      case 'SUPERTREND': {
        if (!window.bigChart) break;
        inst.series.main = window.bigChart.addLineSeries({
          color: '#4FAF7B',
          lineWidth: 0,
          lineVisible: false,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: false
        });
        break;
      }
      case 'PSAR': {
        if (!window.bigChart) break;
        // Two dot-only series (no connecting line) using LWC's native
        // pointMarkersVisible — the project-supported mechanism for exactly
        // this "dots, not a line" style, so zoom/pan/resize are handled by
        // the chart itself rather than a manually-redrawn canvas overlay.
        inst.series.up = window.bigChart.addLineSeries({
          color: '#4FAF7B',
          lineVisible: false,
          pointMarkersVisible: true,
          pointMarkersRadius: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        inst.series.down = window.bigChart.addLineSeries({
          color: '#EF5350',
          lineVisible: false,
          pointMarkersVisible: true,
          pointMarkersRadius: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        break;
      }
      case 'PIVOTPOINTS': {
        if (!window.bigChart) break;
        // TradingView draws each period's pivot levels as a DISCRETE
        // horizontal segment — a new period's line never threads
        // diagonally into the previous period's different value. A plain
        // LWC line series always connects every point it has, so (like
        // Supertrend elsewhere in this file) these series are kept
        // invisible and used only for priceToCoordinate() math; the actual
        // visible segments + labels are hand-drawn per period in
        // _drawOverlayClouds, matching Supertrend's own technique.
        var invisible = { lineWidth: 0, lineVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)' };
        PIVOT_LEVEL_KEYS.forEach(function (k) {
          inst.series[k] = window.bigChart.addLineSeries(Object.assign({}, invisible));
        });
        break;
      }
      case 'PIVOTHIGHLOW': {
        if (!window.bigChart) break;
        // A single invisible line series exists purely to carry LWC's
        // native marker API (setMarkers) — the project-supported mechanism
        // for exactly this "labeled point at a specific candle" case,
        // avoiding any manual DOM positioning. Removing this series (the
        // generic _removeSeries loop already does this for every
        // indicator) discards its markers too, so cleanup needs no special
        // casing beyond what every other indicator already gets.
        inst.series.markers = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'MARKETSTRUCTURE': {
        if (!window.bigChart) break;
        // BOS/CHoCH/MSS segments and swing labels are entirely canvas-drawn
        // (variable-count events, unlike Pivot Points' fixed level set) —
        // this single invisible series exists only so removeIndicator's
        // generic _removeSeries loop has something to clean up and so a
        // price-scale-aware series is always available even before
        // window.bigCandleSeries exists. Coordinate math in
        // _drawOverlayClouds uses window.bigCandleSeries directly (same
        // fallback Supertrend's cloud rendering already relies on), since
        // priceToCoordinate()/timeToCoordinate() work identically on any
        // series sharing the main chart's price/time scale.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'FVG': {
        if (!window.bigChart) break;
        // Zones are canvas-drawn rectangles (variable count, arbitrary
        // top/bottom per zone) — same reasoning as Market Structure's
        // invisible anchor above: this series exists only for
        // removeIndicator's generic cleanup and as a safe fallback price
        // scale before window.bigCandleSeries exists.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'OB': {
        if (!window.bigChart) break;
        // Same reasoning as FVG's anchor: zones are canvas-drawn
        // rectangles, this series only exists for generic cleanup and a
        // safe fallback price scale.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'LIQUIDITY': {
        if (!window.bigChart) break;
        // Pools are canvas-drawn horizontal segments + labels (variable
        // count), same reasoning as Pivot Points' invisible level series.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'LIQUIDITYSWEEPS': {
        if (!window.bigChart) break;
        // Sweep markers/labels are canvas-drawn (variable count), same
        // reasoning as every other event-based indicator's anchor above.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'PREMIUMDISCOUNT': {
        if (!window.bigChart) break;
        // Premium/Discount zones + equilibrium/range lines are entirely
        // canvas-drawn (a variable-length range history), same reasoning
        // as every other zone-based indicator's anchor above.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'SMC': {
        if (!window.bigChart) break;
        // Setup markers/labels are canvas-drawn (variable count), same
        // reasoning as every other event-based indicator's anchor above.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'BREAKERMITIGATION': {
        if (!window.bigChart) break;
        // Breaker/Mitigation zones are canvas-drawn rectangles (variable
        // count), same reasoning as FVG/Order Blocks' own anchor above.
        inst.series.anchor = window.bigChart.addLineSeries({
          lineWidth: 0, lineVisible: false, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, color: 'rgba(0,0,0,0)'
        });
        break;
      }
      case 'DONCHIAN': {
        if (!window.bigChart) break;
        // Colors and style match TradingView's own "Donchian Channels"
        // Pine Script exactly: Upper/Lower both #2962FF, Basis (middle)
        // #FF6D00, all solid lines with their last-value badge shown —
        // not tied to inst.color, since the reference indicator hardcodes
        // these regardless of any per-instance color concept.
        inst.series.upper  = window.bigChart.addLineSeries(_lineOpts('#2962FF', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.lower  = window.bigChart.addLineSeries(_lineOpts('#2962FF', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.middle = window.bigChart.addLineSeries(_lineOpts('#FF6D00', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'KELTNER': {
        if (!window.bigChart) break;
        // Same channel color template as Donchian (upper/lower blue, basis
        // orange) — TradingView's own Keltner Channels default uses this
        // exact scheme. No fill: three plain lines only (see file header
        // note on this indicator for why a canvas fill was deliberately
        // skipped).
        inst.series.upper  = window.bigChart.addLineSeries(_lineOpts('#2962FF', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.lower  = window.bigChart.addLineSeries(_lineOpts('#2962FF', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.middle = window.bigChart.addLineSeries(_lineOpts('#FF6D00', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'BB': {
        if (!window.bigChart) break;
        inst.series.basis = window.bigChart.addLineSeries(_lineOpts('#2196F3', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, false));
        inst.series.upper = window.bigChart.addLineSeries(_lineOpts('#EF5350', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, false));
        inst.series.lower = window.bigChart.addLineSeries(_lineOpts('#26A69A', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, false));
        break;
      }
      case 'VWAP': {
        if (!window.bigChart) break;
        inst.series.main = window.bigChart.addLineSeries(_lineOpts(inst.color || '#6B8FD6', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        if (inst.params.bands === 'On') {
          inst.series.upper = window.bigChart.addLineSeries(_lineOpts('#4CAF50', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
          inst.series.lower = window.bigChart.addLineSeries(_lineOpts('#4CAF50', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        }
        break;
      }
      case 'RSI': {
        var pane = PaneManager.getOrCreate('RSI', 'RSI (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'RSI';
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        var dsh = LWC.LineStyle ? LWC.LineStyle.Dashed : 1;
        inst.series.rsi    = pane.chart.addLineSeries(_lineOpts('#B388FF', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.upper  = pane.chart.addLineSeries(_lineOpts('rgba(242,54,69,0.6)', 1, dsh, false));
        inst.series.lower  = pane.chart.addLineSeries(_lineOpts('rgba(8,153,129,0.6)', 1, dsh, false));
        inst.series.middle = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        break;
      }
      case 'STOCH': {
        var pane = PaneManager.getOrCreate('STOCH', 'Stoch (' + inst.params.kPeriod + ', ' + inst.params.kSmooth + ', ' + inst.params.dPeriod + ')');
        if (!pane) break;
        inst.paneId = 'STOCH';
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        var dsh = LWC.LineStyle ? LWC.LineStyle.Dashed : 1;
        inst.series.k      = pane.chart.addLineSeries(_lineOpts('#2A75FF', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.d      = pane.chart.addLineSeries(_lineOpts('#FF9800', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.upper  = pane.chart.addLineSeries(_lineOpts('rgba(242,54,69,0.6)', 1, dsh, false));
        inst.series.lower  = pane.chart.addLineSeries(_lineOpts('rgba(8,153,129,0.6)', 1, dsh, false));
        inst.series.middle = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        break;
      }
      case 'DMI': {
        var pDi = inst.params.diLength || 14;
        var pAdx = inst.params.adxSmoothing || 14;
        var pane = PaneManager.getOrCreate('DMI', 'DMI (' + pDi + ', ' + pAdx + ')');
        if (!pane) break;
        inst.paneId = 'DMI';
        inst.series.anchor  = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.adx     = pane.chart.addLineSeries(_lineOpts('#E91E63', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.plusDi  = pane.chart.addLineSeries(_lineOpts('#2196F3', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.minusDi = pane.chart.addLineSeries(_lineOpts('#FF9800', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'ADX': {
        var pAdx = inst.params.adxSmoothing || inst.params.length || 14;
        var pDi  = inst.params.diLength || 14;
        var pane = PaneManager.getOrCreate('ADX', 'ADX (' + pAdx + ', ' + pDi + ')');
        if (!pane) break;
        inst.paneId = 'ADX';
        var dsh = LWC.LineStyle ? LWC.LineStyle.Dashed : 1;
        inst.series.adx   = pane.chart.addLineSeries(_lineOpts('#E91E63', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.ref25 = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dsh, false));
        break;
      }
      case 'MACD': {
        var pane = PaneManager.getOrCreate('MACD', 'MACD (' + (inst.params.source || 'close') + ', ' + inst.params.fast + ', ' + inst.params.slow + ', ' + inst.params.signal + ')');
        if (!pane) break;
        inst.paneId = 'MACD';
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        inst.series.histogram = pane.chart.addHistogramSeries({
          color: '#26a69a',
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        });
        inst.series.macd   = pane.chart.addLineSeries({
          color: '#2962FF',
          lineWidth: 1.5,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: false
        });
        inst.series.signal = pane.chart.addLineSeries({
          color: '#FF6D00',
          lineWidth: 1.5,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: false
        });
        inst.series.zero   = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        break;
      }
      case 'ATR': {
        var pane = PaneManager.getOrCreate('ATR', 'ATR (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'ATR';
        inst.series.anchor = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.atr = pane.chart.addLineSeries(_lineOpts(inst.color || '#5C9CE6', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'OBV': {
        var pane = PaneManager.getOrCreate('OBV', 'OBV');
        if (!pane) break;
        inst.paneId = 'OBV';
        inst.series.anchor = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.main   = pane.chart.addLineSeries(_lineOpts(inst.color || '#2962FF', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'CCI': {
        var pane = PaneManager.getOrCreate('CCI', 'CCI (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'CCI';
        var dsh = LWC.LineStyle ? LWC.LineStyle.Dashed : 1;
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        inst.series.anchor    = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.cci       = pane.chart.addLineSeries(_lineOpts(inst.color || '#2A75FF', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.cciSmooth = pane.chart.addLineSeries(_lineOpts('#FFCA28', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.upper     = pane.chart.addLineSeries(_lineOpts('rgba(255,255,255,0.22)', 1, dsh, false));
        inst.series.zero      = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        inst.series.lower     = pane.chart.addLineSeries(_lineOpts('rgba(255,255,255,0.22)', 1, dsh, false));
        break;
      }
      case 'WILLIAMSR': {
        var pane = PaneManager.getOrCreate('WILLIAMSR', 'Williams %R (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'WILLIAMSR';
        var dsh = LWC.LineStyle ? LWC.LineStyle.Dashed : 1;
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        // Two invisible bounds lock the scale to [0, -100] so the line always renders
        inst.series.top    = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.bottom = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        // Plain line for the WR curve itself — the purple highlight is a
        // static band between the -20/-80 reference levels (drawn by
        // _drawPaneClouds, same technique as the Stochastic channel fill),
        // not a fill hugging the curve's own shape.
        inst.series.wr     = pane.chart.addLineSeries(_lineOpts(inst.color || '#855EC9', 2, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.ob     = pane.chart.addLineSeries(_lineOpts('rgba(255,255,255,0.22)', 1, dsh, false));
        inst.series.mid    = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        inst.series.os     = pane.chart.addLineSeries(_lineOpts('rgba(255,255,255,0.22)', 1, dsh, false));
        break;
      }
      case 'MFI': {
        var pane = PaneManager.getOrCreate('MFI', 'MFI (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'MFI';
        var dsh = LWC.LineStyle ? LWC.LineStyle.Dashed : 1;
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        // Two invisible bounds lock the scale to [0, 100] so the line always renders
        inst.series.top    = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.bottom = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.mfi    = pane.chart.addLineSeries(_lineOpts(inst.color || '#B388FF', 2, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.ob     = pane.chart.addLineSeries(_lineOpts('rgba(255,255,255,0.22)', 1, dsh, false));
        inst.series.mid    = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        inst.series.os     = pane.chart.addLineSeries(_lineOpts('rgba(255,255,255,0.22)', 1, dsh, false));
        break;
      }
      case 'ROC': {
        var pane = PaneManager.getOrCreate('ROC', 'ROC (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'ROC';
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        inst.series.roc    = pane.chart.addLineSeries(_lineOpts(inst.color || '#2962FF', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.signal = pane.chart.addLineSeries(_lineOpts('#FF9800', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.zero   = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        break;
      }
      case 'AROON': {
        var pane = PaneManager.getOrCreate('AROON', 'Aroon (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'AROON';
        // Two invisible bounds lock the scale to [0, 100], same technique
        // WilliamsR/MFI use — Aroon's own reference render (see the
        // screenshot this was built from) has no dashed reference lines,
        // just the two Up/Down lines, so no ob/mid/os series here.
        inst.series.top    = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.bottom = pane.chart.addLineSeries(_lineOpts('rgba(0,0,0,0)', 1, 0, false));
        inst.series.up     = pane.chart.addLineSeries(_lineOpts('#FF9800', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.down   = pane.chart.addLineSeries(_lineOpts('#2962FF', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'CMF': {
        var pane = PaneManager.getOrCreate('CMF', 'CMF (' + inst.params.length + ')');
        if (!pane) break;
        inst.paneId = 'CMF';
        var dot = LWC.LineStyle ? LWC.LineStyle.Dotted : 2;
        inst.series.zero = pane.chart.addLineSeries(_lineOpts('rgba(120,123,134,0.35)', 1, dot, false));
        inst.series.cmf  = pane.chart.addLineSeries(_lineOpts(inst.color || '#26A69A', 1.5, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'ICHIMOKU': {
        if (!window.bigChart) break;
        // Main-chart overlay — no separate pane, uses the main price scale.
        // spanA/spanB are kept thin+subtle: the Kumo fill (drawn by
        // _drawOverlayClouds) is the primary visual, these lines are just
        // the boundary edges TradingView also shows.
        inst.series.tenkan = window.bigChart.addLineSeries(_lineOpts('#EF5350', 1.2, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.kijun  = window.bigChart.addLineSeries(_lineOpts('#2962FF', 1.2, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        inst.series.spanA  = window.bigChart.addLineSeries(_lineOpts('rgba(79,175,123,0.55)', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, false));
        inst.series.spanB  = window.bigChart.addLineSeries(_lineOpts('rgba(239,83,80,0.55)',  1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, false));
        inst.series.chikou = window.bigChart.addLineSeries(_lineOpts('#B39DDB', 1.2, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
        break;
      }
      case 'VOLUME': {
        if (!window.bigChart) break;
        // Volume MA rendered on main chart's volume price scale
        inst.series.volMa = window.bigChart.addLineSeries({
          color: inst.color,
          lineWidth: 1.5,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          priceScaleId: ''
        });
        try {
          inst.series.volMa.priceScale().applyOptions({ scaleMargins: { top: 0.7, bottom: 0 } });
        } catch (e) {}
        break;
      }
    }
  }

  function _removeSeries(inst) {
    var def = inst.def;
    if (def.type === 'overlay' || def.type === 'volume') {
      for (var k in inst.series) {
        try { if (window.bigChart && inst.series[k]) window.bigChart.removeSeries(inst.series[k]); } catch (e) {}
      }
    } else if (def.type === 'pane' && inst.paneId) {
      var pane = PaneManager.get(inst.paneId);
      if (pane) {
        for (var k in inst.series) {
          try { if (inst.series[k]) pane.chart.removeSeries(inst.series[k]); } catch (e) {}
        }
      }
      // Remove pane if no other instance uses it
      var usersLeft = Object.values(_instances).filter(function (x) { return x !== inst && x.paneId === inst.paneId; });
      if (usersLeft.length === 0) PaneManager.remove(inst.paneId);
    }
    inst.series = {};
    _drawOverlayClouds();
  }

  // ═══════════════════════════════════════════════════
  // FULL CALCULATION (on data load / param change)
  // ═══════════════════════════════════════════════════

  function _getEffectiveCandles() {
    var candles = [];
    if (window._chartCandles && Array.isArray(window._chartCandles) && window._chartCandles.length > 0) {
      candles = window._chartCandles.slice();
    } else if (_candles && _candles.length > 0) {
      candles = _candles.slice();
    }
    if (window._formingCandles) {
      var fc = window._formingCandles['d'] || window._formingCandles['i'];
      if (fc && fc.time !== undefined && fc.close !== undefined) {
        if (candles.length > 0) {
          var last = candles[candles.length - 1];
          if (last.time === fc.time) {
            candles[candles.length - 1] = {
              time: fc.time,
              open: fc.open !== undefined ? fc.open : last.open,
              high: fc.high !== undefined ? fc.high : last.high,
              low:  fc.low  !== undefined ? fc.low  : last.low,
              close: fc.close,
              volume: fc.volume !== undefined ? fc.volume : last.volume
            };
          } else {
            candles.push({
              time: fc.time,
              open: fc.open || fc.close,
              high: fc.high || fc.close,
              low:  fc.low  || fc.close,
              close: fc.close,
              volume: fc.volume || 0
            });
          }
        } else {
          candles.push(fc);
        }
      }
    }
    return candles;
  }

  // Full recompute + full re-render for Ichimoku, shared by _calcAll (load /
  // param change) and _updateLast (live tick). Ichimoku's forward-projected
  // Span A/B points and backward Chikou point can each shift on every tick
  // (they depend on the still-forming last candle), and they don't live at
  // the chart's "last" time slot — so a targeted .update() isn't safe here.
  // A full setData() on ~5 lightweight line series per tick is correctness-
  // first and cheap at realistic candle counts; documented as a deliberate
  // tradeoff rather than a premature-optimization target.
  function _renderIchimoku(inst) {
    var p = inst.params;
    var calc = Calc.ichimoku(_candles, p.conversionPeriod, p.basePeriod, p.spanBPeriod, p.displacement);
    var disp = Calc.ichimokuDisplay(_candles, calc);
    inst._lastIchimoku = disp;

    if (inst.series.tenkan) { try { inst.series.tenkan.setData(_seriesDataFromPoints(disp.tenkan)); } catch (e) {} }
    if (inst.series.kijun)  { try { inst.series.kijun.setData(_seriesDataFromPoints(disp.kijun)); } catch (e) {} }
    if (inst.series.spanA)  { try { inst.series.spanA.setData(_seriesDataFromPoints(disp.spanA)); } catch (e) {} }
    if (inst.series.spanB)  { try { inst.series.spanB.setData(_seriesDataFromPoints(disp.spanB)); } catch (e) {} }
    if (inst.series.chikou) { try { inst.series.chikou.setData(_seriesDataFromPoints(disp.chikou)); } catch (e) {} }

    _drawOverlayClouds();
  }

  // Full recompute + full re-render for PSAR, shared by _calcAll and
  // _updateLast. PSAR's current (forming) candle can flip from the "up"
  // dot series to the "down" one (or back) as its High/Low change tick to
  // tick — a targeted .update() would leave a stale dot on the series the
  // point just left, since update() can only add/overwrite a point, never
  // remove one. A full setData() on both series avoids that duplicate-dot
  // risk entirely; PSAR's O(n) single-pass recurrence is cheap enough that
  // this is a correctness-first tradeoff, not a real performance cost.
  function _renderPsar(inst) {
    var p = inst.params;
    var res = Calc.psar(_candles, p.initialAF, p.increment, p.maximumAF);
    var times = _candles.map(function (c) { return c.time; });
    inst._dataByTime = {};
    for (var i = 0; i < times.length; i++) {
      inst._dataByTime[times[i]] = { sar: res.sar[i], trend: res.trend[i] };
    }
    var upVals = res.sar.map(function (v, i) { return res.trend[i] === 1 ? v : null; });
    var downVals = res.sar.map(function (v, i) { return res.trend[i] === -1 ? v : null; });
    if (inst.series.up)   { try { inst.series.up.setData(_seriesData(times, upVals)); } catch (e) {} }
    if (inst.series.down) { try { inst.series.down.setData(_seriesData(times, downVals)); } catch (e) {} }
  }

  var PIVOT_LEVEL_KEYS = ['p', 'r1', 'r2', 'r3', 'r4', 'r5', 's1', 's2', 's3', 's4', 's5'];

  // Full recompute + full re-render for Pivot Points, shared by _calcAll
  // and _updateLast. Levels only change when a NEW trading period begins
  // (Calc.pivotPoints always derives the current period's levels from the
  // previous COMPLETED period), so recomputing on every live tick does not
  // make the current session's displayed levels move — they're mathematically
  // pinned until period rollover. A full setData() keeps this simple and
  // correct without a separate "did the period change" tracking mechanism.
  function _renderPivotPoints(inst) {
    var p = inst.params;
    var times = _candles.map(function (c) { return c.time; });
    var res = Calc.pivotPoints(_candles, p.method, p.period);
    inst._dataByTime = {};
    for (var i = 0; i < times.length; i++) {
      var rec = {};
      for (var j = 0; j < PIVOT_LEVEL_KEYS.length; j++) rec[PIVOT_LEVEL_KEYS[j]] = res[PIVOT_LEVEL_KEYS[j]][i];
      inst._dataByTime[times[i]] = rec;
    }
    for (var j = 0; j < PIVOT_LEVEL_KEYS.length; j++) {
      var k = PIVOT_LEVEL_KEYS[j];
      if (inst.series[k]) { try { inst.series[k].setData(_seriesData(times, res[k])); } catch (e) {} }
    }
    // The series above are invisible (coordinate math only) — the actual
    // segmented lines + labels are canvas-drawn in _drawOverlayClouds.
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Pivot Points High Low, shared by
  // _calcAll and _updateLast. Recomputing from scratch every tick is what
  // gives the confirmation-delay semantics for free: Calc.pivotHighLow only
  // ever evaluates indices with rightBars of REAL data after them, so a
  // candidate near the live edge simply isn't in the output yet — no
  // separate "pending confirmation" state to track, and nothing to
  // retract if the live candle's high/low changes before it closes.
  function _renderPivotHighLow(inst) {
    var p = inst.params;
    var times = _candles.map(function (c) { return c.time; });
    var res = Calc.pivotHighLow(_candles, p.leftBars, p.rightBars);
    inst._dataByTime = {};
    var markers = [];
    for (var i = 0; i < times.length; i++) {
      inst._dataByTime[times[i]] = { pivotHigh: res.pivotHigh[i], pivotLow: res.pivotLow[i] };
      if (res.pivotHigh[i] !== null) {
        markers.push({ time: times[i], position: 'aboveBar', color: '#EF5350', shape: 'arrowDown', text: 'PH' });
      }
      if (res.pivotLow[i] !== null) {
        markers.push({ time: times[i], position: 'belowBar', color: '#4FAF7B', shape: 'arrowUp', text: 'PL' });
      }
    }
    markers.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
    // Stored on the instance, not set directly here — _drawOverlayClouds
    // merges every visible PivotHighLow instance's markers and applies them
    // to the real candle series in one call (see comment there for why).
    inst._pivotMarkers = markers;
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Market Structure, shared by
  // _calcAll and _updateLast — same "recompute is cheap, confirmation
  // delay comes for free" reasoning as _renderPivotHighLow above.
  function _renderMarketStructure(inst) {
    var p = inst.params;
    var res = Calc.marketStructure(_candles, p.swingLength, p.confirmation, p.displacementMultiplier, p.atrLength);
    inst._lastMarketStructure = res;

    var markers = [];
    if (p.showSwingLabels === 'On') {
      for (var s = 0; s < res.swings.length; s++) {
        var sw = res.swings[s];
        if (!sw.classification) continue;
        var bullish = (sw.classification === 'HH' || sw.classification === 'HL');
        markers.push({
          time: sw.time,
          position: sw.type === 'high' ? 'aboveBar' : 'belowBar',
          color: bullish ? '#4FAF7B' : '#EF5350',
          shape: 'circle',
          text: sw.classification
        });
      }
    }
    markers.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
    // Merged into the shared setMarkers() call in _drawOverlayClouds
    // alongside Pivot Points High Low's markers, same reasoning as there.
    inst._msMarkers = markers;

    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Fair Value Gap, shared by _calcAll
  // and _updateLast — same "recompute is cheap, forward-looking mitigation
  // comes for free" reasoning as _renderMarketStructure above.
  function _renderFVG(inst) {
    var p = inst.params;
    inst._lastFVG = Calc.fvg(_candles, p.minSizeATRMultiplier, p.atrLength, p.mitigation, p.maxZones);
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Order Blocks, shared by _calcAll
  // and _updateLast — same reasoning as _renderFVG above. Calc.orderBlocks
  // calls Calc.marketStructure itself (reusing the engine/function, never
  // duplicating its logic) with this instance's OWN swingLength.
  function _renderOB(inst) {
    var p = inst.params;
    var maxZ = Math.max(1000, parseInt(p.maxZones, 10) || 50);
    inst._lastOB = Calc.orderBlocks(_candles, p.swingLength, p.mitigation, maxZ);
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Liquidity, shared by _calcAll and
  // _updateLast — same reasoning as _renderFVG/_renderOB above.
  function _renderLiquidity(inst) {
    var p = inst.params;
    var maxP = Math.max(1000, parseInt(p.maxPools, 10) || 50);
    inst._lastLiquidity = Calc.liquidity(_candles, p.swingLength, p.toleranceATRMultiplier, p.atrLength, p.minTouches, maxP);
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Liquidity Sweeps, shared by
  // _calcAll and _updateLast — same reasoning as _renderLiquidity above.
  function _renderLiquiditySweeps(inst) {
    var p = inst.params;
    var maxE = Math.max(1000, parseInt(p.maxEvents, 10) || 50);
    inst._lastLiquiditySweeps = Calc.liquiditySweeps(_candles, p.swingLength, p.toleranceATRMultiplier, p.atrLength, p.minTouches, p.confirmation, maxE);
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Premium & Discount, shared by
  // _calcAll and _updateLast — same reasoning as above.
  function _renderPremiumDiscount(inst) {
    var p = inst.params;
    inst._lastPremiumDiscount = Calc.premiumDiscount(_candles, p.swingLength);
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for SMC Setups, shared by _calcAll and
  // _updateLast — same reasoning as every other composite engine above.
  function _renderSMC(inst) {
    var p = inst.params;
    inst._lastSMC = Calc.smcSetups(_candles, p.swingLength, p.toleranceATRMultiplier, p.atrLength, p.minTouches, p.mitigation, p.confirmation, p.structureWindowBars, p.minScore, p.maxSetups);
    _drawOverlayClouds();
  }

  // Full recompute + full re-render for Breaker & Mitigation Blocks,
  // shared by _calcAll and _updateLast — same reasoning as every other
  // composite engine above.
  function _renderBreakerMitigation(inst) {
    var p = inst.params;
    inst._lastBreakerMitigation = Calc.breakerMitigation(_candles, p.swingLength, p.breakerMitigation, p.maxBlocks);
    _drawOverlayClouds();
  }

  /**
   * Shifts a value array's DISPLAY time by `offset` bars — positive moves
   * points forward (into synthetic future time, same technique Ichimoku
   * uses for its Span A/B projection), negative moves them backward
   * (dropping any point that would land before the dataset's first
   * candle, rather than fabricating a pre-history time). Matches Pine
   * Script's `plot(..., offset=...)`: an offset only changes WHERE a
   * value is drawn, never what data produced it, so this carries no
   * look-ahead-bias concern — every value was already computed from
   * historical data only, by Calc.donchian itself.
   */
  function _offsetSeriesData(candles, times, vals, offset) {
    var n = candles.length;
    if (offset === 0) return _seriesData(times, vals);
    // Reuses Ichimoku's format-aware time helpers (they're generic time-
    // math, not Ichimoku-specific, despite the name) so this works
    // correctly regardless of whether candle.time is a number, a
    // 'yyyy-mm-dd' string, or a {year,month,day} object.
    var interval = Calc._ichimokuBarInterval(candles);
    var lastTime = times[n - 1];
    var lastEpoch = Calc._ichimokuTimeToEpoch(lastTime);
    var out = [];
    var lastOutTime = null;
    for (var i = 0; i < n; i++) {
      var v = vals[i];
      if (v === null || v === undefined || !isFinite(v)) continue;
      var displayIdx = i + offset;
      if (displayIdx < 0) continue;
      var displayTime;
      if (displayIdx < n) {
        displayTime = times[displayIdx];
      } else {
        var futureEpoch = lastEpoch + (displayIdx - (n - 1)) * interval;
        displayTime = Calc._ichimokuEpochToTimeLike(futureEpoch, lastTime);
      }
      if (lastOutTime !== null) {
        if (typeof displayTime === 'number' && typeof lastOutTime === 'number' && displayTime <= lastOutTime) continue;
        if (typeof displayTime === 'string' && typeof lastOutTime === 'string' && displayTime <= lastOutTime) continue;
      }
      out.push({ time: displayTime, value: Number(v) });
      lastOutTime = displayTime;
    }
    return out;
  }

  function _renderDonchian(inst) {
    var p = inst.params;
    var times = _candles.map(function (c) { return c.time; });
    var res = Calc.donchian(_candles, p.length);
    inst._dataByTime = {};
    for (var i = 0; i < times.length; i++) {
      inst._dataByTime[times[i]] = { upper: res.upper[i], lower: res.lower[i], middle: res.middle[i] };
    }

    var offset = parseInt(p.offset, 10) || 0;
    var upperPts  = _offsetSeriesData(_candles, times, res.upper, offset);
    var lowerPts  = _offsetSeriesData(_candles, times, res.lower, offset);
    var middlePts = _offsetSeriesData(_candles, times, res.middle, offset);

    if (inst.series.upper)  { try { inst.series.upper.setData(upperPts); } catch (e) {} }
    if (inst.series.lower)  { try { inst.series.lower.setData(lowerPts); } catch (e) {} }
    if (inst.series.middle) { try { inst.series.middle.setData(middlePts); } catch (e) {} }

    // Display-space (offset-applied) upper/lower points for the canvas
    // fill in _drawOverlayClouds — the fill must track the same shifted
    // positions as the visible lines, not the raw unshifted calculation.
    inst._donchianDisplay = { upper: upperPts, lower: lowerPts };
    _drawOverlayClouds();
  }

  function _calcAll(inst) {
    _candles = _getEffectiveCandles();
    _candleByTime = {};
    _candles.forEach(function (c) { if (c && c.time !== undefined) _candleByTime[c.time] = c; });
    if (!_candles || _candles.length === 0) return;
    // Verify series is initialized
    if (!inst.series || Object.keys(inst.series).length === 0) {
      _createSeries(inst);
    }
    var times = _candles.map(function (c) { return c.time; });
    var src   = Calc.source(_candles, inst.params.source || 'close');
    var p     = inst.params;

    switch (inst.def.id) {
      case 'SMA': {
        var vals = Calc.sma(src, p.length);
        if (inst.series.main) {
          try { inst.series.main.setData(_seriesData(times, vals)); } catch(e) {}
        }
        break;
      }
      case 'EMA': {
        var vals = Calc.ema(src, p.length);
        if (inst.series.main) {
          try { inst.series.main.setData(_seriesData(times, vals)); } catch(e) {}
        }
        break;
      }
      case 'SUPERTREND': {
        var res = Calc.supertrend(_candles, p.length, p.multiplier);
        inst._lastSupertrend = res;
        inst._dataByTime = {};
        var lastValidVal = null;
        var lastValidTrend = null;
        for (var i = 0; i < times.length; i++) {
          var t = times[i];
          var stVal = res.supertrend[i];
          var trVal = res.trend[i];
          inst._dataByTime[t] = { supertrend: stVal, trend: trVal, upper: res.upper[i], lower: res.lower[i] };
          if (stVal !== null && isFinite(stVal)) {
            lastValidVal = stVal;
            lastValidTrend = trVal;
          }
        }
        if (inst.series.main) {
          try {
            if (lastValidVal !== null) {
              inst.series.main.applyOptions({
                color: (lastValidTrend === 1 ? '#4FAF7B' : '#EF5350')
              });
              var n = _candles.length;
              inst.series.main.setData([{ time: _candles[n - 1].time, value: Number(lastValidVal) }]);
            } else {
              inst.series.main.setData([]);
            }
          } catch(e) {}
        }
        _drawOverlayClouds();
        break;
      }
      case 'PSAR': {
        _renderPsar(inst);
        break;
      }
      case 'PIVOTPOINTS': {
        _renderPivotPoints(inst);
        break;
      }
      case 'PIVOTHIGHLOW': {
        _renderPivotHighLow(inst);
        break;
      }
      case 'MARKETSTRUCTURE': {
        _renderMarketStructure(inst);
        break;
      }
      case 'FVG': {
        _renderFVG(inst);
        break;
      }
      case 'OB': {
        _renderOB(inst);
        break;
      }
      case 'LIQUIDITY': {
        _renderLiquidity(inst);
        break;
      }
      case 'LIQUIDITYSWEEPS': {
        _renderLiquiditySweeps(inst);
        break;
      }
      case 'PREMIUMDISCOUNT': {
        _renderPremiumDiscount(inst);
        break;
      }
      case 'SMC': {
        _renderSMC(inst);
        break;
      }
      case 'BREAKERMITIGATION': {
        _renderBreakerMitigation(inst);
        break;
      }
      case 'DONCHIAN': {
        _renderDonchian(inst);
        break;
      }
      case 'BB': {
        var bb = Calc.bollingerBands(src, p.length, p.stdDev);
        inst._lastBb = bb;
        if (inst.series.basis) { try { inst.series.basis.setData(_seriesData(times, bb.basis)); } catch(e) {} }
        if (inst.series.upper) { try { inst.series.upper.setData(_seriesData(times, bb.upper)); } catch(e) {} }
        if (inst.series.lower) { try { inst.series.lower.setData(_seriesData(times, bb.lower)); } catch(e) {} }
        _drawBbCloud();
        break;
      }
      case 'KELTNER': {
        var kc = Calc.keltner(_candles, p.length, p.atrLength, p.multiplier);
        inst._lastKeltner = kc;
        if (inst.series.middle) { try { inst.series.middle.setData(_seriesData(times, kc.middle)); } catch(e) {} }
        if (inst.series.upper)  { try { inst.series.upper.setData(_seriesData(times, kc.upper)); } catch(e) {} }
        if (inst.series.lower)  { try { inst.series.lower.setData(_seriesData(times, kc.lower)); } catch(e) {} }
        _drawOverlayClouds();
        break;
      }
      case 'VWAP': {
        var res = Calc.vwap(_candles, p.source || 'hlc3', p.anchor || 'Session', p.bandMult || 1.0, p.bands === 'On');
        inst._lastVwap = res;
        if (inst.series.main) {
          try { inst.series.main.setData(_seriesData(times, res.vwap)); } catch (e) {}
        }
        if (p.bands === 'On') {
          if (!inst.series.upper && window.bigChart) inst.series.upper = window.bigChart.addLineSeries(_lineOpts('#4CAF50', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
          if (!inst.series.lower && window.bigChart) inst.series.lower = window.bigChart.addLineSeries(_lineOpts('#4CAF50', 1, LWC.LineStyle ? LWC.LineStyle.Solid : 0, true));
          if (inst.series.upper) {
            try { inst.series.upper.setData(_seriesData(times, res.upper)); } catch (e) {}
          }
          if (inst.series.lower) {
            try { inst.series.lower.setData(_seriesData(times, res.lower)); } catch (e) {}
          }
        } else {
          if (inst.series.upper) { try { window.bigChart.removeSeries(inst.series.upper); } catch (e) {} delete inst.series.upper; }
          if (inst.series.lower) { try { window.bigChart.removeSeries(inst.series.lower); } catch (e) {} delete inst.series.lower; }
        }
        _drawOverlayClouds();
        break;
      }
      case 'RSI': {
        var vals = Calc.rsi(src, p.length);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { rsi: vals[i] };
        }
        var uLvl = times.map(function (t) { return { time: t, value: Number(p.upper) }; });
        var lLvl = times.map(function (t) { return { time: t, value: Number(p.lower) }; });
        var mLvl = times.map(function (t) { return { time: t, value: 50 }; });

        var rsiData = _seriesData(times, vals);
        var uData   = _seriesData(times, uLvl.map(function(x){return x.value;}));
        var lData   = _seriesData(times, lLvl.map(function(x){return x.value;}));
        var mData   = _seriesData(times, mLvl.map(function(x){return x.value;}));

        if (inst.series.rsi)    { try { inst.series.rsi.setData(rsiData); } catch(e) {} }
        if (inst.series.upper)  { try { inst.series.upper.setData(uData); } catch(e) {} }
        if (inst.series.lower)  { try { inst.series.lower.setData(lData); } catch(e) {} }
        if (inst.series.middle) { try { inst.series.middle.setData(mData); } catch(e) {} }
        _syncPane(inst.paneId);
        var lastRsi = vals.length > 0 ? vals[vals.length - 1] : null;
        if (lastRsi !== null) {
          PaneManager.updateLegend(inst.paneId, '<span style="color:#B388FF;font-weight:600;">' + lastRsi.toFixed(2) + '</span>');
        }
        break;
      }
      case 'STOCH': {
        var res = Calc.stochastic(_candles, p.kPeriod, p.kSmooth, p.dPeriod);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { k: res.k[i], d: res.d[i], rawK: res.rawK[i] };
        }
        var uLvl = times.map(function (t) { return { time: t, value: 80 }; });
        var lLvl = times.map(function (t) { return { time: t, value: 20 }; });
        var mLvl = times.map(function (t) { return { time: t, value: 50 }; });

        var kData = _seriesData(times, res.k);
        var dData = _seriesData(times, res.d);
        var uData = _seriesData(times, uLvl.map(function(x){return x.value;}));
        var lData = _seriesData(times, lLvl.map(function(x){return x.value;}));
        var mData = _seriesData(times, mLvl.map(function(x){return x.value;}));

        if (inst.series.k)      { try { inst.series.k.setData(kData); } catch(e) {} }
        if (inst.series.d)      { try { inst.series.d.setData(dData); } catch(e) {} }
        if (inst.series.upper)  { try { inst.series.upper.setData(uData); } catch(e) {} }
        if (inst.series.lower)  { try { inst.series.lower.setData(lData); } catch(e) {} }
        if (inst.series.middle) { try { inst.series.middle.setData(mData); } catch(e) {} }
        _syncPane(inst.paneId);
        var lastK = res.k.length > 0 ? res.k[res.k.length - 1] : null;
        var lastD = res.d.length > 0 ? res.d[res.d.length - 1] : null;
        if (lastK !== null || lastD !== null) {
          var html = (lastK !== null && isFinite(lastK) ? '<span style="color:#2A75FF;font-weight:600;margin-right:6px;">' + Number(lastK).toFixed(2) + '</span>' : '') +
                     (lastD !== null && isFinite(lastD) ? '<span style="color:#FF9800;font-weight:600;">' + Number(lastD).toFixed(2) + '</span>' : '');
          PaneManager.updateLegend(inst.paneId, html);
        }
        break;
      }
      case 'DMI': {
        var pDi = inst.params.diLength || 14;
        var pAdx = inst.params.adxSmoothing || 14;
        var res = Calc.adx(_candles, pDi, pAdx);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { adx: res.adx[i], plusDi: res.plusDi[i], minusDi: res.minusDi[i], dx: res.dx[i] };
        }
        var anchorData  = times.map(function (t) { return { time: t, value: 0 }; });
        var adxData     = _seriesData(times, res.adx);
        var plusDiData  = _seriesData(times, res.plusDi);
        var minusDiData = _seriesData(times, res.minusDi);

        if (inst.series.anchor)  { try { inst.series.anchor.setData(anchorData); } catch(e) {} }
        if (inst.series.adx)     { try { inst.series.adx.setData(adxData); } catch(e) {} }
        if (inst.series.plusDi)  { try { inst.series.plusDi.setData(plusDiData); } catch(e) {} }
        if (inst.series.minusDi) { try { inst.series.minusDi.setData(minusDiData); } catch(e) {} }
        _syncPane(inst.paneId);
        var lastAdx = res.adx.length > 0 ? res.adx[res.adx.length - 1] : null;
        var lastPlus = res.plusDi.length > 0 ? res.plusDi[res.plusDi.length - 1] : null;
        var lastMinus = res.minusDi.length > 0 ? res.minusDi[res.minusDi.length - 1] : null;
        if (lastAdx !== null || lastPlus !== null || lastMinus !== null) {
          var html = (lastAdx !== null && isFinite(lastAdx) ? '<span style="color:#E91E63;font-weight:600;margin-right:6px;">' + Number(lastAdx).toFixed(4) + '</span>' : '') +
                     (lastPlus !== null && isFinite(lastPlus) ? '<span style="color:#2196F3;font-weight:600;margin-right:6px;">' + Number(lastPlus).toFixed(4) + '</span>' : '') +
                     (lastMinus !== null && isFinite(lastMinus) ? '<span style="color:#FF9800;font-weight:600;">' + Number(lastMinus).toFixed(4) + '</span>' : '');
          PaneManager.updateLegend(inst.paneId, html);
        }
        break;
      }
      case 'ADX': {
        var pAdx = inst.params.adxSmoothing || inst.params.length || 14;
        var pDi  = inst.params.diLength || 14;
        var res = Calc.adx(_candles, pDi, pAdx);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { adx: res.adx[i], plusDi: res.plusDi[i], minusDi: res.minusDi[i], dx: res.dx[i] };
        }
        var ref25Data = times.map(function (t) { return { time: t, value: 25 }; });
        var adxData   = _seriesData(times, res.adx);
        var r25Data   = _seriesData(times, ref25Data.map(function (x) { return x.value; }));

        if (inst.series.adx)   { try { inst.series.adx.setData(adxData); } catch(e) {} }
        if (inst.series.ref25) { try { inst.series.ref25.setData(r25Data); } catch(e) {} }
        _syncPane(inst.paneId);
        var lastAdx = res.adx.length > 0 ? res.adx[res.adx.length - 1] : null;
        if (lastAdx !== null && isFinite(lastAdx)) {
          var html = '<span style="color:#E91E63;font-weight:600;">' + Number(lastAdx).toFixed(2) + '</span>';
          PaneManager.updateLegend(inst.paneId, html);
        }
        break;
      }
      case 'MACD': {
        var res  = Calc.macd(src, p.fast, p.slow, p.signal);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { macd: res.macd[i], signal: res.signal[i], histogram: res.histogram[i] };
        }
        var hist = [];
        var lastHistTime = null;
        var prevH = null;
        for (var i = 0; i < times.length; i++) {
          var t = times[i];
          var hVal = res.histogram[i];
          if (t !== null && t !== undefined && hVal !== null && hVal !== undefined && isFinite(hVal)) {
            if (lastHistTime !== null) {
              if (typeof t === 'number' && typeof lastHistTime === 'number' && t <= lastHistTime) continue;
              if (typeof t === 'string' && typeof lastHistTime === 'string' && t <= lastHistTime) continue;
            }
            var col;
            if (hVal >= 0) {
              col = (prevH === null || hVal >= prevH) ? '#26a69a' : 'rgba(38,166,154,0.45)';
            } else {
              col = (prevH === null || hVal <= prevH) ? '#ef5350' : 'rgba(239,83,80,0.45)';
            }
            hist.push({ time: t, value: Number(hVal), color: col });
            lastHistTime = t;
            prevH = hVal;
          }
        }
        var macdData = _seriesData(times, res.macd);
        var sigData  = _seriesData(times, res.signal);
        var zeroData = _seriesData(times, times.map(function () { return 0; }));

        if (inst.series.histogram) { try { inst.series.histogram.setData(hist); } catch(e) {} }
        if (inst.series.macd)      { try { inst.series.macd.setData(macdData); } catch(e) {} }
        if (inst.series.signal)    { try { inst.series.signal.setData(sigData); } catch(e) {} }
        if (inst.series.zero)      { try { inst.series.zero.setData(zeroData); } catch(e) {} }
        _syncPane(inst.paneId);
        var lastM = res.macd.length > 0 ? res.macd[res.macd.length - 1] : null;
        var lastS = res.signal.length > 0 ? res.signal[res.signal.length - 1] : null;
        var lastH = res.histogram.length > 0 ? res.histogram[res.histogram.length - 1] : null;
        if (lastM !== null || lastS !== null || lastH !== null) {
          var hCol = (lastH !== null && lastH >= 0) ? '#26a69a' : '#ef5350';
          var html = (lastH !== null ? '<span style="color:' + hCol + ';margin-right:6px;">' + (lastH >= 0 ? '+' : '') + lastH.toFixed(2) + '</span>' : '') +
                     (lastM !== null ? '<span style="color:#2962FF;margin-right:6px;">' + lastM.toFixed(2) + '</span>' : '') +
                     (lastS !== null ? '<span style="color:#FF6D00;">' + lastS.toFixed(2) + '</span>' : '');
          PaneManager.updateLegend(inst.paneId, html);
        }
        break;
      }
      case 'ATR': {
        var vals = Calc.atr(_candles, p.length);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { atr: vals[i] };
        }
        var anchorData = times.map(function (t) { return { time: t, value: 0 }; });
        var atrData = _seriesData(times, vals);
        if (inst.series.anchor) { try { inst.series.anchor.setData(anchorData); } catch (e) {} }
        if (inst.series.atr)    { try { inst.series.atr.setData(atrData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastAtr = vals.length > 0 ? vals[vals.length - 1] : null;
        if (lastAtr !== null && lastAtr !== undefined && isFinite(lastAtr)) {
          PaneManager.updateLegend(inst.paneId, '<span style="color:#5C9CE6;font-weight:600;">' + Number(lastAtr).toFixed(2) + '</span>');
        }
        break;
      }
      case 'OBV': {
        var vals = Calc.obv(_candles);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { obv: vals[i] };
        }
        var anchorData = times.map(function (t) { return { time: t, value: 0 }; });
        var obvData = _seriesData(times, vals);
        if (inst.series.anchor) { try { inst.series.anchor.setData(anchorData); } catch (e) {} }
        if (inst.series.main)   { try { inst.series.main.setData(obvData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastObv = vals.length > 0 ? vals[vals.length - 1] : null;
        if (lastObv !== null && lastObv !== undefined && isFinite(lastObv)) {
          PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#2962FF') + ';font-weight:600;">' + Number(lastObv).toLocaleString('en-US') + '</span>');
        }
        break;
      }
      case 'CCI': {
        var vals = Calc.cci(_candles, p.length);
        var smoothVals = Calc.smoothIgnoringNulls(vals, p.smoothLength);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { cci: vals[i], cciSmooth: smoothVals[i] };
        }
        var anchorData      = times.map(function (t) { return { time: t, value: 0 }; });
        var cciData         = _seriesData(times, vals);
        var cciSmoothData   = _seriesData(times, smoothVals);
        var upperData       = _seriesData(times, times.map(function () { return 100; }));
        var zeroData        = _seriesData(times, times.map(function () { return 0; }));
        var lowerData       = _seriesData(times, times.map(function () { return -100; }));

        if (inst.series.anchor)    { try { inst.series.anchor.setData(anchorData); } catch (e) {} }
        if (inst.series.cci)       { try { inst.series.cci.setData(cciData); } catch (e) {} }
        if (inst.series.cciSmooth) { try { inst.series.cciSmooth.setData(cciSmoothData); } catch (e) {} }
        if (inst.series.upper)     { try { inst.series.upper.setData(upperData); } catch (e) {} }
        if (inst.series.zero)      { try { inst.series.zero.setData(zeroData); } catch (e) {} }
        if (inst.series.lower)     { try { inst.series.lower.setData(lowerData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastCci = vals.length > 0 ? vals[vals.length - 1] : null;
        var lastCciSmooth = smoothVals.length > 0 ? smoothVals[smoothVals.length - 1] : null;
        var cciHtml = (lastCci !== null && isFinite(lastCci) ? '<span style="color:' + (inst.color || '#2A75FF') + ';font-weight:600;margin-right:6px;">' + Number(lastCci).toFixed(2) + '</span>' : '') +
                      (lastCciSmooth !== null && isFinite(lastCciSmooth) ? '<span style="color:#FFCA28;font-weight:600;">' + Number(lastCciSmooth).toFixed(2) + '</span>' : '');
        PaneManager.updateLegend(inst.paneId, cciHtml);
        break;
      }
      case 'WILLIAMSR': {
        var vals = Calc.williamsR(_candles, p.length);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { wr: vals[i] };
        }
        var topData    = times.map(function (t) { return { time: t, value: 0 }; });
        var bottomData = times.map(function (t) { return { time: t, value: -100 }; });
        var wrData     = _seriesData(times, vals);
        var obData     = _seriesData(times, times.map(function () { return -20; }));
        var midData    = _seriesData(times, times.map(function () { return -50; }));
        var osData     = _seriesData(times, times.map(function () { return -80; }));

        if (inst.series.top)    { try { inst.series.top.setData(topData); } catch (e) {} }
        if (inst.series.bottom) { try { inst.series.bottom.setData(bottomData); } catch (e) {} }
        if (inst.series.wr)     { try { inst.series.wr.setData(wrData); } catch (e) {} }
        if (inst.series.ob)     { try { inst.series.ob.setData(obData); } catch (e) {} }
        if (inst.series.mid)    { try { inst.series.mid.setData(midData); } catch (e) {} }
        if (inst.series.os)     { try { inst.series.os.setData(osData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastWr = vals.length > 0 ? vals[vals.length - 1] : null;
        if (lastWr !== null && isFinite(lastWr)) {
          PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#855EC9') + ';font-weight:600;">' + Number(lastWr).toFixed(2) + '</span>');
        }
        break;
      }
      case 'MFI': {
        var vals = Calc.moneyFlowIndex(_candles, p.length);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { mfi: vals[i] };
        }
        var topData    = times.map(function (t) { return { time: t, value: 100 }; });
        var bottomData = times.map(function (t) { return { time: t, value: 0 }; });
        var mfiData    = _seriesData(times, vals);
        var obData     = _seriesData(times, times.map(function () { return 80; }));
        var midData    = _seriesData(times, times.map(function () { return 50; }));
        var osData     = _seriesData(times, times.map(function () { return 20; }));

        if (inst.series.top)    { try { inst.series.top.setData(topData); } catch (e) {} }
        if (inst.series.bottom) { try { inst.series.bottom.setData(bottomData); } catch (e) {} }
        if (inst.series.mfi)    { try { inst.series.mfi.setData(mfiData); } catch (e) {} }
        if (inst.series.ob)     { try { inst.series.ob.setData(obData); } catch (e) {} }
        if (inst.series.mid)    { try { inst.series.mid.setData(midData); } catch (e) {} }
        if (inst.series.os)     { try { inst.series.os.setData(osData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastMfi = vals.length > 0 ? vals[vals.length - 1] : null;
        if (lastMfi !== null && isFinite(lastMfi)) {
          PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#B388FF') + ';font-weight:600;">' + Number(lastMfi).toFixed(2) + '</span>');
        }
        break;
      }
      case 'ROC': {
        var res = Calc.roc(src, p.length, p.signalPeriod);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { roc: res.roc[i], signal: res.signal[i] };
        }
        var rocData    = _seriesData(times, res.roc);
        var signalData = _seriesData(times, res.signal);
        var zeroData   = _seriesData(times, times.map(function () { return 0; }));
        if (inst.series.roc)    { try { inst.series.roc.setData(rocData); } catch (e) {} }
        if (inst.series.signal) { try { inst.series.signal.setData(signalData); } catch (e) {} }
        if (inst.series.zero)   { try { inst.series.zero.setData(zeroData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastRoc = res.roc.length > 0 ? res.roc[res.roc.length - 1] : null;
        var lastSignal = res.signal.length > 0 ? res.signal[res.signal.length - 1] : null;
        if (lastRoc !== null || lastSignal !== null) {
          var rocHtml = (lastRoc !== null && isFinite(lastRoc) ? '<span style="color:' + (inst.color || '#2962FF') + ';font-weight:600;margin-right:6px;">' + Number(lastRoc).toFixed(2) + '</span>' : '') +
                        (lastSignal !== null && isFinite(lastSignal) ? '<span style="color:#FF9800;font-weight:600;">' + Number(lastSignal).toFixed(2) + '</span>' : '');
          PaneManager.updateLegend(inst.paneId, rocHtml);
        }
        break;
      }
      case 'AROON': {
        var res = Calc.aroon(_candles, p.length);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { up: res.up[i], down: res.down[i], osc: res.osc[i] };
        }
        var topData    = times.map(function (t) { return { time: t, value: 100 }; });
        var bottomData = times.map(function (t) { return { time: t, value: 0 }; });
        var upData     = _seriesData(times, res.up);
        var downData   = _seriesData(times, res.down);
        if (inst.series.top)    { try { inst.series.top.setData(topData); } catch (e) {} }
        if (inst.series.bottom) { try { inst.series.bottom.setData(bottomData); } catch (e) {} }
        if (inst.series.up)     { try { inst.series.up.setData(upData); } catch (e) {} }
        if (inst.series.down)   { try { inst.series.down.setData(downData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastUp = res.up.length > 0 ? res.up[res.up.length - 1] : null;
        var lastDown = res.down.length > 0 ? res.down[res.down.length - 1] : null;
        if (lastUp !== null || lastDown !== null) {
          var aroonHtml = (lastUp !== null && isFinite(lastUp) ? '<span style="color:#FF9800;font-weight:600;margin-right:6px;">' + Number(lastUp).toFixed(2) + '</span>' : '') +
                          (lastDown !== null && isFinite(lastDown) ? '<span style="color:#2962FF;font-weight:600;">' + Number(lastDown).toFixed(2) + '</span>' : '');
          PaneManager.updateLegend(inst.paneId, aroonHtml);
        }
        break;
      }
      case 'CMF': {
        var vals = Calc.cmf(_candles, p.length);
        inst._dataByTime = {};
        for (var i = 0; i < times.length; i++) {
          inst._dataByTime[times[i]] = { cmf: vals[i] };
        }
        var zeroData = _seriesData(times, times.map(function () { return 0; }));
        var cmfData  = _seriesData(times, vals);
        if (inst.series.zero) { try { inst.series.zero.setData(zeroData); } catch (e) {} }
        if (inst.series.cmf)  { try { inst.series.cmf.setData(cmfData); } catch (e) {} }
        _syncPane(inst.paneId);
        var lastCmf = vals.length > 0 ? vals[vals.length - 1] : null;
        if (lastCmf !== null && isFinite(lastCmf)) {
          PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#26A69A') + ';font-weight:600;">' + Number(lastCmf).toFixed(2) + '</span>');
        }
        break;
      }
      case 'ICHIMOKU': {
        _renderIchimoku(inst);
        break;
      }
      case 'VOLUME': {
        var vols = _candles.map(function (c) { return Number(c.volume) || 0; });
        var vals = Calc.sma(vols, p.maLength);
        if (inst.series.volMa) {
          try { inst.series.volMa.setData(_seriesData(times, vals)); } catch(e) {}
        }
        break;
      }
    }
    PaneManager.updateTimeScales();
  }

  function _syncPane(paneId) {
    if (!paneId || !window.bigChart) return;
    var pane = PaneManager.get(paneId);
    if (!pane || !pane.chart) return;
    var lr = window.bigChart.timeScale().getVisibleLogicalRange();
    if (lr) {
      try { pane.chart.timeScale().setVisibleLogicalRange({ from: lr.from, to: lr.to }); } catch (e) {}
    }
    requestAnimationFrame(function () {
      if (window.bigChart && pane && pane.chart) {
        var curLr = window.bigChart.timeScale().getVisibleLogicalRange();
        if (curLr) {
          try { pane.chart.timeScale().setVisibleLogicalRange({ from: curLr.from, to: curLr.to }); } catch (e) {}
        }
      }
      _drawOverlayClouds();
    });
  }

  // ═══════════════════════════════════════════════════
  // INCREMENTAL UPDATE (forming candle tick)
  // ═══════════════════════════════════════════════════

  function _updateLast(inst) {
    if (!_candles || _candles.length === 0) return;
    var n = _candles.length;
    var lastTime = _candles[n - 1].time;
    var src = Calc.source(_candles, inst.params.source || 'close');
    var p = inst.params;

    try {
      switch (inst.def.id) {
        case 'SMA': {
          if (n < p.length) break;
          var sum = 0;
          for (var j = n - p.length; j < n; j++) sum += src[j];
          var v = sum / p.length;
          if (inst.series.main) inst.series.main.update({ time: lastTime, value: v });
          break;
        }
        case 'EMA': {
          // Full O(n) recalc from proper seed — avoids re-seeding divergence.
          // Only the last value is pushed to the chart via update() (cheap render path).
          var vals = Calc.ema(src, p.length);
          var last = vals[vals.length - 1];
          if (last !== null && inst.series.main) inst.series.main.update({ time: lastTime, value: last });
          break;
        }
        case 'SUPERTREND': {
          var res = Calc.supertrend(_candles, p.length, p.multiplier);
          inst._lastSupertrend = res;
          var lastSt = res.supertrend[n - 1];
          var lastTr = res.trend[n - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { supertrend: lastSt, trend: lastTr, upper: res.upper[n - 1], lower: res.lower[n - 1] };

          if (inst.series.main && lastSt !== null && isFinite(lastSt)) {
            try {
              inst.series.main.applyOptions({
                color: (lastTr === 1 ? '#4FAF7B' : '#EF5350')
              });
              inst.series.main.update({ time: lastTime, value: Number(lastSt) });
            } catch(e) {}
          }
          _drawOverlayClouds();
          break;
        }
        case 'PSAR': {
          _renderPsar(inst);
          break;
        }
        case 'PIVOTPOINTS': {
          _renderPivotPoints(inst);
          break;
        }
        case 'PIVOTHIGHLOW': {
          _renderPivotHighLow(inst);
          break;
        }
        case 'MARKETSTRUCTURE': {
          _renderMarketStructure(inst);
          break;
        }
        case 'FVG': {
          _renderFVG(inst);
          break;
        }
        case 'OB': {
          _renderOB(inst);
          break;
        }
        case 'LIQUIDITY': {
          _renderLiquidity(inst);
          break;
        }
        case 'LIQUIDITYSWEEPS': {
          _renderLiquiditySweeps(inst);
          break;
        }
        case 'PREMIUMDISCOUNT': {
          _renderPremiumDiscount(inst);
          break;
        }
        case 'SMC': {
          _renderSMC(inst);
          break;
        }
        case 'BREAKERMITIGATION': {
          _renderBreakerMitigation(inst);
          break;
        }
        case 'DONCHIAN': {
          _renderDonchian(inst);
          break;
        }
        case 'BB': {
          if (n < p.length) break;
          var slice = src.slice(n - p.length);
          var mean = 0;
          for (var j = 0; j < slice.length; j++) mean += slice[j];
          mean /= p.length;
          var variance = 0;
          for (var j = 0; j < slice.length; j++) { var d = slice[j] - mean; variance += d * d; }
          var sd = Math.sqrt(variance / p.length);
          if (inst.series.basis) inst.series.basis.update({ time: lastTime, value: mean });
          if (inst.series.upper) inst.series.upper.update({ time: lastTime, value: mean + p.stdDev * sd });
          if (inst.series.lower) inst.series.lower.update({ time: lastTime, value: mean - p.stdDev * sd });
          if (inst._lastBb) {
            inst._lastBb.basis[n - 1] = mean;
            inst._lastBb.upper[n - 1] = mean + p.stdDev * sd;
            inst._lastBb.lower[n - 1] = mean - p.stdDev * sd;
          }
          _drawBbCloud();
          break;
        }
        case 'KELTNER': {
          // Full O(n) recalc, like Supertrend/EMA/RSI above — both EMA and
          // ATR are recurrence relations seeded from history, so only the
          // current candle's inputs changing still requires replaying the
          // whole series to get an exact (not re-seeded/approximated) value.
          // Only the last point is pushed to the chart via update().
          var kc = Calc.keltner(_candles, p.length, p.atrLength, p.multiplier);
          inst._lastKeltner = kc;
          var lastMid = kc.middle[n - 1];
          var lastUp  = kc.upper[n - 1];
          var lastLo  = kc.lower[n - 1];
          if (lastMid !== null && inst.series.middle) inst.series.middle.update({ time: lastTime, value: lastMid });
          if (lastUp  !== null && inst.series.upper)  inst.series.upper.update({ time: lastTime, value: lastUp });
          if (lastLo  !== null && inst.series.lower)  inst.series.lower.update({ time: lastTime, value: lastLo });
          _drawOverlayClouds();
          break;
        }
        case 'VWAP': {
          var res = Calc.vwap(_candles, p.source || 'hlc3', p.anchor || 'Session', p.bandMult || 1.0, p.bands === 'On');
          inst._lastVwap = res;
          var lastV = res.vwap[n - 1];
          if (inst.series.main && lastV !== null) inst.series.main.update({ time: lastTime, value: lastV });
          if (p.bands === 'On') {
            var lastU = res.upper[n - 1];
            var lastL = res.lower[n - 1];
            if (inst.series.upper && lastU !== null) inst.series.upper.update({ time: lastTime, value: lastU });
            if (inst.series.lower && lastL !== null) inst.series.lower.update({ time: lastTime, value: lastL });
          }
          _drawOverlayClouds();
          break;
        }
        case 'RSI': {
          // Full O(n) recalc from proper seed for exact Wilder's smoothing.
          var vals = Calc.rsi(src, p.length);
          var last = vals[vals.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { rsi: last };
          if (last !== null && inst.series.rsi) inst.series.rsi.update({ time: lastTime, value: last });
          if (inst.series.upper) inst.series.upper.update({ time: lastTime, value: Number(p.upper) });
          if (inst.series.lower) inst.series.lower.update({ time: lastTime, value: Number(p.lower) });
          if (inst.series.middle) inst.series.middle.update({ time: lastTime, value: 50 });
          if (last !== null) {
            PaneManager.updateLegend(inst.paneId, '<span style="color:#B388FF;font-weight:600;">' + last.toFixed(2) + '</span>');
          }
          _drawOverlayClouds();
          break;
        }
        case 'STOCH': {
          var res = Calc.stochastic(_candles, p.kPeriod, p.kSmooth, p.dPeriod);
          var lastK = res.k[n - 1];
          var lastD = res.d[n - 1];
          var lastRaw = res.rawK[n - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { k: lastK, d: lastD, rawK: lastRaw };

          if (lastK !== null && isFinite(lastK) && inst.series.k) {
            inst.series.k.update({ time: lastTime, value: Number(lastK) });
          }
          if (lastD !== null && isFinite(lastD) && inst.series.d) {
            inst.series.d.update({ time: lastTime, value: Number(lastD) });
          }
          if (inst.series.upper)  inst.series.upper.update({ time: lastTime, value: 80 });
          if (inst.series.lower)  inst.series.lower.update({ time: lastTime, value: 20 });
          if (inst.series.middle) inst.series.middle.update({ time: lastTime, value: 50 });

          if (lastK !== null || lastD !== null) {
            var html = (lastK !== null && isFinite(lastK) ? '<span style="color:#2A75FF;font-weight:600;margin-right:6px;">' + Number(lastK).toFixed(2) + '</span>' : '') +
                       (lastD !== null && isFinite(lastD) ? '<span style="color:#FF9800;font-weight:600;">' + Number(lastD).toFixed(2) + '</span>' : '');
            PaneManager.updateLegend(inst.paneId, html);
          }
          _drawOverlayClouds();
          break;
        }
        case 'DMI': {
          var pDi = inst.params.diLength || 14;
          var pAdx = inst.params.adxSmoothing || 14;
          var res = Calc.adx(_candles, pDi, pAdx);
          var lastAdx = res.adx[n - 1];
          var lastPlus = res.plusDi[n - 1];
          var lastMinus = res.minusDi[n - 1];
          var lastDx = res.dx[n - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { adx: lastAdx, plusDi: lastPlus, minusDi: lastMinus, dx: lastDx };

          if (lastAdx !== null && isFinite(lastAdx) && inst.series.adx) {
            inst.series.adx.update({ time: lastTime, value: Number(lastAdx) });
          }
          if (lastPlus !== null && isFinite(lastPlus) && inst.series.plusDi) {
            inst.series.plusDi.update({ time: lastTime, value: Number(lastPlus) });
          }
          if (lastMinus !== null && isFinite(lastMinus) && inst.series.minusDi) {
            inst.series.minusDi.update({ time: lastTime, value: Number(lastMinus) });
          }

          if (lastAdx !== null || lastPlus !== null || lastMinus !== null) {
            var html = (lastAdx !== null && isFinite(lastAdx) ? '<span style="color:#E91E63;font-weight:600;margin-right:6px;">' + Number(lastAdx).toFixed(4) + '</span>' : '') +
                       (lastPlus !== null && isFinite(lastPlus) ? '<span style="color:#2196F3;font-weight:600;margin-right:6px;">+' + Number(lastPlus).toFixed(4) + '</span>' : '') +
                       (lastMinus !== null && isFinite(lastMinus) ? '<span style="color:#FF9800;font-weight:600;">-' + Number(lastMinus).toFixed(4) + '</span>' : '');
            PaneManager.updateLegend(inst.paneId, html);
          }
          _drawOverlayClouds();
          break;
        }
        case 'ADX': {
          var pAdx = inst.params.adxSmoothing || inst.params.length || 14;
          var pDi  = inst.params.diLength || 14;
          var res = Calc.adx(_candles, pDi, pAdx);
          var lastAdx = res.adx[n - 1];
          var lastDx = res.dx[n - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { adx: lastAdx, dx: lastDx };

          if (lastAdx !== null && isFinite(lastAdx) && inst.series.adx) {
            inst.series.adx.update({ time: lastTime, value: Number(lastAdx) });
          }
          if (inst.series.ref25) inst.series.ref25.update({ time: lastTime, value: 25 });

          if (lastAdx !== null && isFinite(lastAdx)) {
            var html = '<span style="color:#E91E63;font-weight:600;">' + Number(lastAdx).toFixed(2) + '</span>';
            PaneManager.updateLegend(inst.paneId, html);
          }
          _drawOverlayClouds();
          break;
        }
        case 'MACD': {
          // Full O(n) calculation to ensure continuous signal line without sub-slice re-seeding divergence
          var res = Calc.macd(src, p.fast, p.slow, p.signal);
          var ml = res.macd[res.macd.length - 1];
          var sl = res.signal[res.signal.length - 1];
          var hl = res.histogram[res.histogram.length - 1];
          var prevH = res.histogram.length > 1 ? res.histogram[res.histogram.length - 2] : null;
          var hCol = '#26a69a';
          if (hl !== null) {
            if (hl >= 0) hCol = (prevH === null || hl >= prevH) ? '#26a69a' : 'rgba(38,166,154,0.45)';
            else hCol = (prevH === null || hl <= prevH) ? '#ef5350' : 'rgba(239,83,80,0.45)';
          }
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { macd: ml, signal: sl, histogram: hl };
          if (ml !== null && inst.series.macd)   inst.series.macd.update({ time: lastTime, value: ml });
          if (sl !== null && inst.series.signal)  inst.series.signal.update({ time: lastTime, value: sl });
          if (hl !== null && inst.series.histogram) inst.series.histogram.update({
            time: lastTime, value: hl, color: hCol
          });
          if (inst.series.zero) inst.series.zero.update({ time: lastTime, value: 0 });
          if (ml !== null || sl !== null || hl !== null) {
            var html = (hl !== null ? '<span style="color:' + hCol + ';margin-right:6px;">' + (hl >= 0 ? '+' : '') + hl.toFixed(2) + '</span>' : '') +
                       (ml !== null ? '<span style="color:#2962FF;margin-right:6px;">' + ml.toFixed(2) + '</span>' : '') +
                       (sl !== null ? '<span style="color:#FF6D00;">' + sl.toFixed(2) + '</span>' : '');
            PaneManager.updateLegend(inst.paneId, html);
          }
          break;
        }
        case 'ATR': {
          var vals = Calc.atr(_candles, p.length);
          var last = vals[vals.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { atr: last };
          if (last !== null && isFinite(last) && inst.series.atr) {
            inst.series.atr.update({ time: lastTime, value: last });
          }
          if (last !== null && isFinite(last)) {
            PaneManager.updateLegend(inst.paneId, '<span style="color:#5C9CE6;font-weight:600;">' + Number(last).toFixed(2) + '</span>');
          }
          break;
        }
        case 'OBV': {
          var vals = Calc.obv(_candles);
          var last = vals[vals.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { obv: last };
          if (last !== null && isFinite(last) && inst.series.main) {
            inst.series.main.update({ time: lastTime, value: last });
          }
          if (last !== null && isFinite(last)) {
            PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#2962FF') + ';font-weight:600;">' + Number(last).toLocaleString('en-US') + '</span>');
          }
          break;
        }
        case 'CCI': {
          var vals = Calc.cci(_candles, p.length);
          var smoothVals = Calc.smoothIgnoringNulls(vals, p.smoothLength);
          var last = vals[vals.length - 1];
          var lastSmooth = smoothVals[smoothVals.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { cci: last, cciSmooth: lastSmooth };
          if (last !== null && isFinite(last) && inst.series.cci) {
            inst.series.cci.update({ time: lastTime, value: Number(last) });
          }
          if (lastSmooth !== null && isFinite(lastSmooth) && inst.series.cciSmooth) {
            inst.series.cciSmooth.update({ time: lastTime, value: Number(lastSmooth) });
          }
          if (inst.series.upper) inst.series.upper.update({ time: lastTime, value: 100 });
          if (inst.series.zero)  inst.series.zero.update({ time: lastTime, value: 0 });
          if (inst.series.lower) inst.series.lower.update({ time: lastTime, value: -100 });
          var cciHtml = (last !== null && isFinite(last) ? '<span style="color:' + (inst.color || '#2A75FF') + ';font-weight:600;margin-right:6px;">' + Number(last).toFixed(2) + '</span>' : '') +
                        (lastSmooth !== null && isFinite(lastSmooth) ? '<span style="color:#FFCA28;font-weight:600;">' + Number(lastSmooth).toFixed(2) + '</span>' : '');
          PaneManager.updateLegend(inst.paneId, cciHtml);
          break;
        }
        case 'WILLIAMSR': {
          var vals = Calc.williamsR(_candles, p.length);
          var last = vals[vals.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { wr: last };
          if (last !== null && isFinite(last) && inst.series.wr) {
            inst.series.wr.update({ time: lastTime, value: last });
          }
          if (inst.series.top)    inst.series.top.update({ time: lastTime, value: 0 });
          if (inst.series.bottom) inst.series.bottom.update({ time: lastTime, value: -100 });
          if (inst.series.ob)     inst.series.ob.update({ time: lastTime, value: -20 });
          if (inst.series.mid)    inst.series.mid.update({ time: lastTime, value: -50 });
          if (inst.series.os)     inst.series.os.update({ time: lastTime, value: -80 });
          if (last !== null && isFinite(last)) {
            PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#855EC9') + ';font-weight:600;">' + Number(last).toFixed(2) + '</span>');
          }
          break;
        }
        case 'MFI': {
          var vals = Calc.moneyFlowIndex(_candles, p.length);
          var last = vals[vals.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { mfi: last };
          if (last !== null && isFinite(last) && inst.series.mfi) {
            inst.series.mfi.update({ time: lastTime, value: last });
          }
          if (inst.series.top)    inst.series.top.update({ time: lastTime, value: 100 });
          if (inst.series.bottom) inst.series.bottom.update({ time: lastTime, value: 0 });
          if (inst.series.ob)     inst.series.ob.update({ time: lastTime, value: 80 });
          if (inst.series.mid)    inst.series.mid.update({ time: lastTime, value: 50 });
          if (inst.series.os)     inst.series.os.update({ time: lastTime, value: 20 });
          if (last !== null && isFinite(last)) {
            PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#B388FF') + ';font-weight:600;">' + Number(last).toFixed(2) + '</span>');
          }
          break;
        }
        case 'ROC': {
          var res = Calc.roc(src, p.length, p.signalPeriod);
          var lastRoc = res.roc[res.roc.length - 1];
          var lastSignal = res.signal[res.signal.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { roc: lastRoc, signal: lastSignal };
          if (lastRoc !== null && isFinite(lastRoc) && inst.series.roc) {
            inst.series.roc.update({ time: lastTime, value: Number(lastRoc) });
          }
          if (lastSignal !== null && isFinite(lastSignal) && inst.series.signal) {
            inst.series.signal.update({ time: lastTime, value: Number(lastSignal) });
          }
          if (inst.series.zero) inst.series.zero.update({ time: lastTime, value: 0 });
          if (lastRoc !== null || lastSignal !== null) {
            var rocHtml = (lastRoc !== null && isFinite(lastRoc) ? '<span style="color:' + (inst.color || '#2962FF') + ';font-weight:600;margin-right:6px;">' + Number(lastRoc).toFixed(2) + '</span>' : '') +
                          (lastSignal !== null && isFinite(lastSignal) ? '<span style="color:#FF9800;font-weight:600;">' + Number(lastSignal).toFixed(2) + '</span>' : '');
            PaneManager.updateLegend(inst.paneId, rocHtml);
          }
          break;
        }
        case 'AROON': {
          var res = Calc.aroon(_candles, p.length);
          var lastUp = res.up[res.up.length - 1];
          var lastDown = res.down[res.down.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { up: lastUp, down: lastDown, osc: res.osc[res.osc.length - 1] };
          if (lastUp !== null && isFinite(lastUp) && inst.series.up) {
            inst.series.up.update({ time: lastTime, value: Number(lastUp) });
          }
          if (lastDown !== null && isFinite(lastDown) && inst.series.down) {
            inst.series.down.update({ time: lastTime, value: Number(lastDown) });
          }
          if (inst.series.top)    inst.series.top.update({ time: lastTime, value: 100 });
          if (inst.series.bottom) inst.series.bottom.update({ time: lastTime, value: 0 });
          if (lastUp !== null || lastDown !== null) {
            var aroonHtml = (lastUp !== null && isFinite(lastUp) ? '<span style="color:#FF9800;font-weight:600;margin-right:6px;">' + Number(lastUp).toFixed(2) + '</span>' : '') +
                            (lastDown !== null && isFinite(lastDown) ? '<span style="color:#2962FF;font-weight:600;">' + Number(lastDown).toFixed(2) + '</span>' : '');
            PaneManager.updateLegend(inst.paneId, aroonHtml);
          }
          break;
        }
        case 'CMF': {
          var vals = Calc.cmf(_candles, p.length);
          var lastCmf = vals[vals.length - 1];
          if (!inst._dataByTime) inst._dataByTime = {};
          inst._dataByTime[lastTime] = { cmf: lastCmf };
          if (lastCmf !== null && isFinite(lastCmf) && inst.series.cmf) {
            inst.series.cmf.update({ time: lastTime, value: Number(lastCmf) });
          }
          if (inst.series.zero) inst.series.zero.update({ time: lastTime, value: 0 });
          if (lastCmf !== null && isFinite(lastCmf)) {
            PaneManager.updateLegend(inst.paneId, '<span style="color:' + (inst.color || '#26A69A') + ';font-weight:600;">' + Number(lastCmf).toFixed(2) + '</span>');
          }
          break;
        }
        case 'ICHIMOKU': {
          _renderIchimoku(inst);
          break;
        }
        case 'VOLUME': {
          if (n < p.maLength) break;
          var vols = _candles.slice(n - p.maLength).map(function (c) { return Number(c.volume) || 0; });
          var avg = 0;
          for (var j = 0; j < vols.length; j++) avg += vols[j];
          avg /= p.maLength;
          if (inst.series.volMa) inst.series.volMa.update({ time: lastTime, value: avg });
          break;
        }
      }
    } catch (e) { /* benign: "Cannot update oldest data" during transitions */ }
  }

  // ═══════════════════════════════════════════════════
  // UI — ACTIVE INDICATORS BAR (legend chips)
  // ═══════════════════════════════════════════════════

  function _refreshBar() {
    var bar = document.getElementById('active-indicators-bar');
    if (!bar) return;
    bar.innerHTML = '';

    for (var id in _instances) {
      (function (instanceId) {
        var inst = _instances[instanceId];
        var chip = document.createElement('div');
        chip.style.cssText = [
          'display:inline-flex', 'align-items:center', 'gap:7px',
          'padding:4px 10px', 'border-radius:14px',
          'background:rgba(30,34,45,0.85)', 'border:1px solid rgba(255,255,255,0.15)',
          'font-size:12px', 'font-weight:500', 'color:#d1d4dc', 'cursor:default',
          'margin-right:6px', 'margin-bottom:4px', 'user-select:none',
          'box-shadow:0 1px 3px rgba(0,0,0,0.3)', 'transition:all 0.15s'
        ].join(';');
        if (!inst.visible) chip.style.opacity = '0.45';

        var dot = document.createElement('span');
        dot.style.cssText = 'width:8px;height:8px;border-radius:50%;flex-shrink:0;background:' + inst.color + ';box-shadow:0 0 4px ' + inst.color + ';';
        chip.appendChild(dot);

        var lbl = document.createElement('span');
        lbl.style.cssText = 'font-weight:600;letter-spacing:0.2px;';
        lbl.textContent = _chipLabel(inst);
        chip.appendChild(lbl);

        var eyeBtn = document.createElement('span');
        eyeBtn.textContent = inst.visible ? '👁' : '🙈';
        eyeBtn.style.cssText = 'cursor:pointer;font-size:13px;opacity:0.85;padding:0 2px;line-height:1;display:inline-flex;align-items:center;transition:opacity 0.15s;';
        eyeBtn.title = inst.visible ? 'Hide' : 'Show';
        eyeBtn.onmouseenter = function () { eyeBtn.style.opacity = '1'; };
        eyeBtn.onmouseleave = function () { eyeBtn.style.opacity = '0.85'; };
        eyeBtn.onclick = function (e) { e.stopPropagation(); IndicatorEngine.toggleIndicator(instanceId); };
        chip.appendChild(eyeBtn);

        var gearBtn = document.createElement('span');
        gearBtn.textContent = '⚙';
        gearBtn.style.cssText = 'cursor:pointer;font-size:13px;color:#9aa0a6;padding:0 2px;line-height:1;display:inline-flex;align-items:center;transition:color 0.15s;';
        gearBtn.title = 'Settings';
        gearBtn.onmouseenter = function () { gearBtn.style.color = '#fff'; };
        gearBtn.onmouseleave = function () { gearBtn.style.color = '#9aa0a6'; };
        gearBtn.onclick = function (e) { e.stopPropagation(); _openSettings(instanceId); };
        chip.appendChild(gearBtn);

        var xBtn = document.createElement('span');
        xBtn.textContent = '×';
        xBtn.style.cssText = 'cursor:pointer;color:#9aa0a6;font-size:16px;font-weight:700;line-height:1;padding:0 2px;display:inline-flex;align-items:center;transition:color 0.15s;';
        xBtn.title = 'Remove';
        xBtn.onmouseenter = function () { xBtn.style.color = '#ff5252'; };
        xBtn.onmouseleave = function () { xBtn.style.color = '#9aa0a6'; };
        xBtn.onclick = function (e) { e.stopPropagation(); IndicatorEngine.removeIndicator(instanceId); };
        chip.appendChild(xBtn);

        bar.appendChild(chip);
      })(id);
    }
  }

  function _chipLabel(inst) {
    var p = inst.params;
    switch (inst.def.id) {
      case 'SMA':        return 'SMA ' + p.length;
      case 'EMA':        return 'EMA ' + p.length;
      case 'SUPERTREND': return 'Supertrend ' + p.length + ',' + p.multiplier;
      case 'PSAR':        return 'PSAR ' + p.initialAF + ',' + p.increment + ',' + p.maximumAF;
      case 'PIVOTPOINTS': return 'Pivot Points (' + p.method + ', ' + p.period + ')';
      case 'PIVOTHIGHLOW': return 'Pivot Points HL (' + p.leftBars + ',' + p.rightBars + ')';
      case 'MARKETSTRUCTURE': return 'Market Structure (' + p.swingLength + ')';
      case 'FVG': return 'Fair Value Gap (' + p.mitigation + ')';
      case 'OB': return 'Order Blocks (' + p.swingLength + ', ' + p.mitigation + ')';
      case 'LIQUIDITY': return 'Liquidity (EQH/EQL) (' + (p.swingLength || 10) + ')';
      case 'LIQUIDITYSWEEPS': return 'Liquidity Sweeps (' + p.confirmation + ')';
      case 'PREMIUMDISCOUNT': return 'Premium & Discount Delta Volume (' + p.swingLength + ')';
      case 'SMC': return 'SMC Setups (' + p.swingLength + ')';
      case 'BREAKERMITIGATION': return 'Breaker & Mitigation (' + p.swingLength + ')';
      case 'DONCHIAN': return 'Donchian Channels (' + p.length + (p.offset ? ', offset ' + p.offset : '') + ')';
      case 'KELTNER': return 'Keltner Channels (' + p.length + ',' + p.atrLength + ',' + p.multiplier + ')';
      case 'RSI':        return 'RSI ' + p.length;
      case 'STOCH':      return 'Stochastic ' + p.kPeriod + ',' + p.kSmooth + ',' + p.dPeriod;
      case 'DMI':        return 'DMI ' + (p.diLength || 14) + ',' + (p.adxSmoothing || 14);
      case 'ADX':        return 'ADX ' + (p.adxSmoothing || 14) + ',' + (p.diLength || 14);
      case 'MACD':       return 'MACD ' + p.fast + ',' + p.slow + ',' + p.signal;
      case 'BB':         return 'BB ' + p.length;
      case 'VWAP':       return 'VWAP ' + (p.source || 'hlc3').toUpperCase();
      case 'ATR':        return 'ATR ' + p.length;
      case 'VOLUME':     return 'Vol MA ' + p.maLength;
      case 'OBV':        return 'OBV';
      case 'CCI':        return 'CCI ' + p.length;
      case 'WILLIAMSR':  return 'Williams %R ' + p.length;
      case 'MFI':        return 'MFI ' + p.length;
      case 'ROC':        return 'ROC ' + p.length + (p.signalPeriod ? ', ' + p.signalPeriod : '');
      case 'AROON':      return 'Aroon ' + p.length;
      case 'CMF':        return 'CMF ' + p.length;
      case 'ICHIMOKU':   return 'Ichimoku ' + p.conversionPeriod + '/' + p.basePeriod + '/' + p.spanBPeriod + '/' + p.displacement;
      default:           return inst.def.name;
    }
  }

  // ═══════════════════════════════════════════════════
  // UI — SETTINGS MODAL
  // ═══════════════════════════════════════════════════

  function _ensureModal() {
    if (document.getElementById('ind-settings-modal')) return;
    var modal = document.createElement('div');
    modal.id = 'ind-settings-modal';
    modal.style.cssText = 'display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.75);z-index:9998;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
    modal.innerHTML =
      '<div style="background:#1e222d;border:1px solid #2a2e39;border-radius:10px;padding:24px;min-width:300px;max-width:380px;width:88%;box-shadow:0 8px 32px rgba(0,0,0,0.7);">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;">' +
          '<span id="ind-settings-title" style="color:#fff;font-weight:700;font-size:14px;"></span>' +
          '<button id="ind-settings-close" style="background:none;border:none;color:#787b86;font-size:22px;cursor:pointer;line-height:1;">&times;</button>' +
        '</div>' +
        '<div id="ind-settings-body" style="display:flex;flex-direction:column;gap:14px;"></div>' +
        '<div style="display:flex;gap:8px;margin-top:20px;">' +
          '<button id="ind-settings-reset" style="padding:9px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:7px;color:#787b86;cursor:pointer;font-size:13px;">Reset</button>' +
          '<button id="ind-settings-cancel" style="flex:1;padding:9px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:7px;color:#d1d4dc;cursor:pointer;font-size:13px;">Cancel</button>' +
          '<button id="ind-settings-apply" style="flex:1;padding:9px;background:#2962FF;border:none;border-radius:7px;color:#fff;cursor:pointer;font-size:13px;font-weight:600;">Apply</button>' +
        '</div>' +
      '</div>';
    modal.addEventListener('click', function (e) { if (e.target === modal) _closeModal(); });
    document.getElementById('ind-settings-close') && document.getElementById('ind-settings-close').addEventListener('click', _closeModal);
    document.body.appendChild(modal);
    document.getElementById('ind-settings-close').addEventListener('click', _closeModal);
    document.getElementById('ind-settings-cancel').addEventListener('click', _closeModal);
  }

  function _closeModal() {
    var m = document.getElementById('ind-settings-modal');
    if (m) m.style.display = 'none';
  }

  function _openSettings(instanceId) {
    _ensureModal();
    var inst = _instances[instanceId];
    if (!inst) return;
    var modal = document.getElementById('ind-settings-modal');
    var title = document.getElementById('ind-settings-title');
    var body  = document.getElementById('ind-settings-body');
    var apply = document.getElementById('ind-settings-apply');
    var reset = document.getElementById('ind-settings-reset');

    title.textContent = inst.def.fullName;
    body.innerHTML = '';

    var pDefs = inst.def.paramDefs || {};
    if (Object.keys(pDefs).length === 0) {
      body.innerHTML = '<div style="color:#787b86;font-size:13px;padding:8px 0;">' + inst.def.fullName + ' has no configurable calculation parameters.</div>';
      apply.onclick = _closeModal;
      reset.style.display = 'none';
      modal.style.display = 'flex';
      return;
    }
    reset.style.display = '';

    var inputs = {};
    for (var key in pDefs) {
      (function (k) {
        var pd = pDefs[k];
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;flex-direction:column;gap:5px;';
        var lbl = document.createElement('label');
        lbl.style.cssText = 'font-size:11px;color:#787b86;text-transform:uppercase;letter-spacing:0.4px;';
        lbl.textContent = pd.label || k;
        row.appendChild(lbl);
        if (pd.type === 'source') {
          var sel = document.createElement('select');
          sel.style.cssText = 'padding:7px 10px;background:#0e1118;border:1px solid #2a2e39;border-radius:6px;color:#d1d4dc;font-size:13px;';
          ['hlc3','close','open','high','low','hl2','ohlc4'].forEach(function (opt) {
            var o = document.createElement('option');
            o.value = opt; o.textContent = opt.toUpperCase();
            if (inst.params[k] === opt) o.selected = true;
            sel.appendChild(o);
          });
          row.appendChild(sel);
          inputs[k] = sel;
        } else if (pd.type === 'select') {
          var sel = document.createElement('select');
          sel.style.cssText = 'padding:7px 10px;background:#0e1118;border:1px solid #2a2e39;border-radius:6px;color:#d1d4dc;font-size:13px;';
          (pd.options || []).forEach(function (opt) {
            var o = document.createElement('option');
            o.value = opt; o.textContent = opt;
            if (String(inst.params[k]) === String(opt)) o.selected = true;
            sel.appendChild(o);
          });
          row.appendChild(sel);
          inputs[k] = sel;
        } else {
          var inp = document.createElement('input');
          inp.type = 'number';
          inp.value = inst.params[k];
          if (pd.min !== undefined) inp.min = pd.min;
          if (pd.max !== undefined) inp.max = pd.max;
          if (pd.type === 'float') inp.step = '0.1';
          inp.style.cssText = 'padding:7px 10px;background:#0e1118;border:1px solid #2a2e39;border-radius:6px;color:#d1d4dc;font-size:13px;';
          row.appendChild(inp);
          inputs[k] = inp;
        }
        body.appendChild(row);
      })(key);
    }

    apply.onclick = function () {
      var newP = {};
      for (var k in pDefs) {
        var pd = pDefs[k];
        var raw = inputs[k].value;
        var val;
        if (pd.type === 'float') val = parseFloat(raw);
        else if (pd.type === 'int') val = parseInt(raw, 10);
        else { newP[k] = raw; continue; }

        // Reject NaN/Infinity (empty field, "abc", etc.) by falling back to
        // the instance's current value rather than pushing garbage into the
        // calculation. Otherwise clamp to the field's documented min/max.
        if (!isFinite(val)) {
          val = inst.params[k];
        } else {
          if (pd.min !== undefined && val < pd.min) val = pd.min;
          if (pd.max !== undefined && val > pd.max) val = pd.max;
        }
        newP[k] = val;
      }
      IndicatorEngine.updateParams(instanceId, newP);
      _closeModal();
    };

    reset.onclick = function () {
      var defaults = {};
      for (var k in pDefs) defaults[k] = pDefs[k].default;
      IndicatorEngine.updateParams(instanceId, defaults);
      _closeModal();
    };

    modal.style.display = 'flex';
  }

  // ═══════════════════════════════════════════════════
  // UI — INDICATOR DROPDOWN MENU
  // ═══════════════════════════════════════════════════

  var MENU_GROUPS = [
    { label: 'Moving Averages',  ids: ['SMA', 'EMA'] },
    { label: 'Trend & Overlays', ids: ['SUPERTREND', 'BB', 'ICHIMOKU', 'PSAR', 'DONCHIAN', 'KELTNER'] },
    { label: 'Support/Resistance', ids: ['PIVOTPOINTS'] },
    { label: 'Market Structure', ids: ['PIVOTHIGHLOW', 'MARKETSTRUCTURE', 'FVG', 'OB', 'LIQUIDITY', 'LIQUIDITYSWEEPS', 'PREMIUMDISCOUNT', 'SMC', 'BREAKERMITIGATION'] },
    { label: 'Oscillators',      ids: ['RSI', 'STOCH', 'DMI', 'ADX', 'MACD', 'ATR', 'CCI', 'WILLIAMSR', 'MFI', 'ROC', 'AROON', 'CMF'] },
    { label: 'Volume',           ids: ['VOLUME', 'VWAP', 'OBV'] }
  ];

  /** True if a search query matches an indicator (by id, short name, or full name). Exported for testing. */
  function _menuItemMatches(def, query) {
    var q = (query || '').trim().toLowerCase();
    if (!q) return true;
    var haystack = (def.id + ' ' + def.name + ' ' + def.fullName).toLowerCase();
    return haystack.indexOf(q) !== -1;
  }

  function _renderMenuItems(container, menu, query) {
    container.innerHTML = '';
    var matchCount = 0;

    MENU_GROUPS.forEach(function (g) {
      var visibleIds = g.ids.filter(function (indId) {
        var def = DEFS[indId];
        return def && _menuItemMatches(def, query);
      });
      if (visibleIds.length === 0) return;

      var hdr = document.createElement('div');
      hdr.style.cssText = 'padding:6px 14px 3px;font-size:10px;color:#787b86;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;';
      hdr.textContent = g.label;
      container.appendChild(hdr);

      visibleIds.forEach(function (indId) {
        var def = DEFS[indId];
        matchCount++;
        var item = document.createElement('div');
        item.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px 14px;cursor:pointer;color:#d1d4dc;font-size:13px;transition:background 0.1s;';
        item.onmouseover = function () { item.style.background = 'rgba(255,255,255,0.06)'; };
        item.onmouseout  = function () { item.style.background = ''; };

        var nameEl = document.createElement('span');
        nameEl.textContent = def.fullName;
        item.appendChild(nameEl);

        var plus = document.createElement('span');
        plus.textContent = '+';
        plus.style.cssText = 'color:#2962FF;font-size:18px;font-weight:700;padding:0 2px;line-height:1;';
        item.appendChild(plus);

        item.onclick = function (e) {
          e.stopPropagation();
          IndicatorEngine.addIndicator(indId);
          menu.style.display = 'none';
        };
        container.appendChild(item);
      });
    });

    if ((query || '').trim() && matchCount === 0) {
      var empty = document.createElement('div');
      empty.style.cssText = 'padding:14px;color:#787b86;font-size:12px;text-align:center;';
      empty.textContent = 'No indicators match "' + query + '"';
      container.appendChild(empty);
    }
  }

  function _buildMenu() {
    var menu = document.getElementById('indicatorMenu');
    if (!menu) return;

    // window.toggleIndicatorMenu flips this element's display between
    // 'block' and 'none' — preserve that on/off state across rebuilds,
    // just rendered as a flex column internally (search box + scroll list).
    var wasOpen = !!(menu.style && (menu.style.display === 'flex' || menu.style.display === 'block'));
    menu.innerHTML = '';
    menu.style.cssText = [
      'position:absolute', 'top:calc(100% + 6px)', 'left:0',
      'background:#1e222d', 'border:1px solid #2a2e39', 'border-radius:8px',
      'min-width:230px', 'max-height:380px', 'z-index:2000', 'box-shadow:0 4px 20px rgba(0,0,0,0.7)',
      'overflow:hidden', 'padding:0', 'flex-direction:column'
    ].join(';');
    menu.style.display = wasOpen ? 'flex' : 'none';

    // Sticky search box — stays visible above the scrollable item list so
    // it's never pushed off-screen by a long indicator list.
    var searchWrap = document.createElement('div');
    searchWrap.style.cssText = 'flex:0 0 auto;padding:8px;border-bottom:1px solid #2a2e39;';
    var searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search indicators…';
    searchInput.value = _menuSearchText;
    searchInput.style.cssText = 'width:100%;box-sizing:border-box;padding:7px 10px;background:#0e1118;border:1px solid #2a2e39;border-radius:6px;color:#d1d4dc;font-size:13px;outline:none;';
    searchInput.onclick = function (e) { e.stopPropagation(); };
    searchWrap.appendChild(searchInput);
    menu.appendChild(searchWrap);

    var itemsWrap = document.createElement('div');
    itemsWrap.style.cssText = 'flex:1 1 auto;overflow-y:auto;padding:6px 0;';
    menu.appendChild(itemsWrap);

    searchInput.oninput = function () {
      _menuSearchText = searchInput.value;
      _renderMenuItems(itemsWrap, menu, _menuSearchText);
    };

    _renderMenuItems(itemsWrap, menu, _menuSearchText);
  }

  // ═══════════════════════════════════════════════════
  // EMA COLOR CYCLING
  // ═══════════════════════════════════════════════════

  var _emaColorIdx = 0;
  function _nextEmaColor() {
    var c = EMA_COLORS[_emaColorIdx % EMA_COLORS.length];
    _emaColorIdx++;
    return c;
  }

  // ═══════════════════════════════════════════════════
  // PUBLIC ENGINE
  // ═══════════════════════════════════════════════════

  var IndicatorEngine = {

    /** Called by dashboard.js after chart data loads */
    onCandlesLoaded: function (candles, range) {
      _candles = candles ? candles.slice() : [];
      _candleByTime = {};
      _candles.forEach(function (c) { if (c && c.time !== undefined) _candleByTime[c.time] = c; });
      _range = range;
      _buildMenu();
      _attachMainCrosshairSync();

      // One-time restore of persisted indicator configuration on cold load.
      // Deliberately guarded so later symbol/timeframe switches (which also
      // call onCandlesLoaded) never re-restore on top of already-active
      // instances — those already persist in-session on their own.
      if (!_persistenceRestored) {
        _persistenceRestored = true;
        var saved = _loadPersistedConfig();
        saved.forEach(function (entry) {
          // One bad saved entry (or a calc error while restoring it) must
          // not abort restoration of the rest of the saved configuration.
          try {
            IndicatorEngine.addIndicator(entry.type, entry.params, {
              visible: entry.visible, color: entry.color, skipSave: true
            });
          } catch (e) { console.error('[IndicatorEngine] failed to restore', entry.type, e); }
        });
      }

      for (var id in _instances) {
        // Isolate each instance's calculation: one indicator's bug must not
        // prevent every other active indicator from recalculating.
        try { _calcAll(_instances[id]); } catch (e) { console.error('[IndicatorEngine] calcAll failed for', id, e); }
      }
      PaneManager.updateTimeScales();
      _refreshBar();
    },

    /** Called by dashboard.js on every live tick */
    onCandleUpdate: function (fc) {
      _candles = _getEffectiveCandles();
      _candleByTime = {};
      _candles.forEach(function (c) { if (c && c.time !== undefined) _candleByTime[c.time] = c; });
      for (var id in _instances) {
        var inst = _instances[id];
        if (inst.visible) {
          _updateLast(inst);
        }
      }
    },

    /** Add a new indicator instance. Returns instanceId.
     *  opts: { visible, color, skipSave } — used internally to restore a
     *  persisted configuration without re-triggering a redundant save. */
    addIndicator: function (indicatorId, params, opts) {
      var def = DEFS[indicatorId];
      if (!def) { console.warn('[IndicatorEngine] Unknown indicator:', indicatorId); return null; }
      opts = opts || {};
      // A live addIndicator call (user action, or the persistence-restore
      // loop itself) means the session's indicator state is now authoritative.
      // Mark restore as done so a *later* first onCandlesLoaded never
      // re-applies the persisted config on top of it and creates duplicates.
      _persistenceRestored = true;

      // Synchronize latest chart candles
      _candles = _getEffectiveCandles();
      _candleByTime = {};
      _candles.forEach(function (c) { if (c && c.time !== undefined) _candleByTime[c.time] = c; });

      // Build default params
      var defaultParams = {};
      for (var k in def.paramDefs) defaultParams[k] = def.paramDefs[k].default;
      var mergedParams = Object.assign({}, defaultParams, params || {});

      // Determine color — an explicit restore color always wins so a
      // restored instance keeps the exact look it had before reload,
      // instead of re-cycling the EMA palette.
      var color = opts.color || (def.id === 'EMA' ? _nextEmaColor() : def.defaultColor);

      var id = _newId();
      var inst = {
        id: id,
        def: def,
        params: mergedParams,
        color: color,
        series: {},
        paneId: null,
        visible: opts.visible !== false
      };
      _instances[id] = inst;
      _createSeries(inst);
      _attachMainCrosshairSync();
      if (_candles.length > 0) {
        try { _calcAll(inst); } catch (e) { console.error('[IndicatorEngine] calcAll failed for', inst.id, e); }
      }
      if (!inst.visible) {
        for (var sk in inst.series) {
          try { inst.series[sk].applyOptions({ visible: false }); } catch (e) {}
        }
      }
      _drawOverlayClouds();
      _refreshBar();
      if (!opts.skipSave) _savePersistedConfig();
      return id;
    },

    /** Remove an indicator instance. */
    removeIndicator: function (instanceId) {
      var inst = _instances[instanceId];
      if (!inst) return;
      var typeId = inst.def.id;
      // Delete from _instances BEFORE _removeSeries: _removeSeries triggers
      // _drawOverlayClouds (pane-usage checks and the Pivot Points High Low
      // marker merge both iterate _instances), so this instance must
      // already be gone from that iteration — otherwise a just-removed
      // PivotHighLow's markers would still be merged back in on this exact
      // redraw and never actually clear from the chart.
      delete _instances[instanceId];
      _removeSeries(inst);
      // Recycle EMA color index on removal (approximate)
      if (inst.def.id === 'EMA') { _emaColorIdx = Math.max(0, _emaColorIdx - 1); }
      // Sync checkmark indicators in stock.html
      if (typeId === 'SMA') { var c = document.getElementById('check_SMA_20'); if (c) c.style.opacity = '0'; }
      if (typeId === 'EMA') { var c = document.getElementById('check_EMA_20'); if (c) c.style.opacity = '0'; }
      if (typeId === 'BB')  { var c = document.getElementById('check_BB');     if (c) c.style.opacity = '0'; }
      _refreshBar();
      _savePersistedConfig();
    },

    /** Toggle visibility of an indicator instance. */
    toggleIndicator: function (instanceId) {
      var inst = _instances[instanceId];
      if (!inst) return;
      inst.visible = !inst.visible;
      for (var k in inst.series) {
        try { inst.series[k].applyOptions({ visible: inst.visible }); } catch (e) {}
      }
      _drawOverlayClouds();
      _refreshBar();
      _savePersistedConfig();
    },

    /** Update parameters and recalculate. */
    updateParams: function (instanceId, params) {
      var inst = _instances[instanceId];
      if (!inst) return;
      inst.params = Object.assign({}, inst.params, params);
      // Update pane title if relevant
      if (inst.paneId) {
        var titles = {
          RSI: 'RSI (' + inst.params.length + ')',
          STOCH: 'Stoch (' + inst.params.kPeriod + ', ' + inst.params.kSmooth + ', ' + inst.params.dPeriod + ')',
          DMI: 'DMI (' + (inst.params.diLength || 14) + ', ' + (inst.params.adxSmoothing || 14) + ')',
          ADX: 'ADX (' + (inst.params.adxSmoothing || 14) + ', ' + (inst.params.diLength || 14) + ')',
          MACD: 'MACD (' + (inst.params.source || 'close') + ', ' + inst.params.fast + ',' + inst.params.slow + ',' + inst.params.signal + ')',
          ATR: 'ATR (' + inst.params.length + ')',
          WILLIAMSR: 'Williams %R (' + inst.params.length + ')',
          MFI: 'MFI (' + inst.params.length + ')',
          ROC: 'ROC (' + inst.params.length + ')',
          AROON: 'Aroon (' + inst.params.length + ')',
          CMF: 'CMF (' + inst.params.length + ')'
        };
        PaneManager.setTitle(inst.paneId, titles[inst.paneId] || inst.paneId);
      }
      if (_candles.length > 0) {
        try { _calcAll(inst); } catch (e) { console.error('[IndicatorEngine] calcAll failed for', inst.id, e); }
      }
      _refreshBar();
      _savePersistedConfig();
    },

    /**
     * Legacy shim: maps old stock.html toggleIndicator('SMA_20', event) calls.
     * If the indicator is already active (one instance), removes it.
     * Otherwise adds it with defaults.
     */
    legacyToggle: function (oldId, event) {
      if (event) event.stopPropagation();
      var map = {
        'SMA_20': ['SMA', { length: 20 }],
        'EMA_20': ['EMA', { length: 20 }],
        'BB':     ['BB',  {}]
      };
      var entry = map[oldId];
      if (!entry) return;
      var indId = entry[0];
      // Find existing instance with same type
      var existing = null;
      for (var id in _instances) {
        if (_instances[id].def.id === indId) { existing = id; break; }
      }
      var check = document.getElementById('check_' + oldId);
      if (existing) {
        this.removeIndicator(existing);
        if (check) check.style.opacity = '0';
      } else {
        this.addIndicator(indId, entry[1]);
        if (check) check.style.opacity = '1';
      }
    },

    DEFS: DEFS,
    getInstances: function () { return Object.assign({}, _instances); },
    PaneManager: PaneManager,
    _Calc: Calc,  // exposed for tests
    _test: {       // exposed for tests — pure, DOM-independent helpers only
      sanitizeConfig: _sanitizeConfig,
      serializeConfig: _serializeConfig,
      menuItemMatches: _menuItemMatches,
      PERSIST_KEY: PERSIST_KEY,
      PERSIST_VERSION: PERSIST_VERSION
    }
  };

  window.IndicatorEngine = IndicatorEngine;

  // Override old stock-ui.js toggleIndicator and toggleIndicatorMenu
  window.toggleIndicator = function (id, event) {
    IndicatorEngine.legacyToggle(id, event);
  };

  window.toggleIndicatorMenu = function toggleIndicatorMenu() {
    var menu = document.getElementById('indicatorMenu');
    if (menu) {
      var isClosed = (menu.style.display === 'none' || !menu.style.display);
      _buildMenu();
      menu.style.display = isClosed ? 'flex' : 'none';
      if (isClosed) {
        var input = menu.querySelector('input');
        if (input) { try { input.focus(); } catch (e) {} }
      } else {
        _menuSearchText = '';
      }
    }
  };

  // Build indicator menu once DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _buildMenu);
  } else {
    setTimeout(_buildMenu, 50);
  }

  // Defensive self-init: stock.html loads dashboard.js BEFORE this script,
  // and dashboard.js's own `if (window.IndicatorEngine) window.IndicatorEngine
  // .onCandlesLoaded(...)` calls are never retried if that guard misses —
  // which happens whenever the chart's candle data resolves from a fast
  // cache-hit path quickly enough that dashboard.js calls it before this
  // script has finished loading. That silently drops both the initial
  // render hookup AND (since restoration only happens inside
  // onCandlesLoaded) the persisted-indicator-config restore, with no error.
  // If candles are already cached on `window` by the time THIS script runs,
  // pull them in ourselves instead of waiting for a call that already
  // happened and won't repeat.
  if (window._chartCandles && window._chartCandles.length > 0) {
    IndicatorEngine.onCandlesLoaded(window._chartCandles, window._lastFormingRange || null);
  }

})();
