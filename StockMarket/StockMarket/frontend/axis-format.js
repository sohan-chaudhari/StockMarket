/*
 * axis-format.js — shared TradingView-style X-axis tick formatter for LEVERAGE.
 *
 * Frontend-only, pure/stateless with respect to chart data: it never mutates
 * candles, never calls the network, and never touches the intraday
 * sequential-index architecture. It only maps a Lightweight Charts tick
 * (time, markType) to a display label, using the real IST date/time.
 *
 * Design:
 *   - one shared implementation used by the big chart, mini chart, market
 *     chart and indicator panes (registered per chart instance).
 *   - honours Lightweight Charts `markType` (Year/Month/DayOfMonth/Time)
 *     and refines it with LEVERAGE trading-session rules.
 *   - real trading-day / month / year boundaries are derived from the real
 *     bar sequence (`_intradayBarTimes`), never from `day == 1|2|3` or
 *     `barsIntoDay < stepBars`.
 *   - future whitespace (sequential index beyond the last real bar) is
 *     suppressed instead of fabricating non-trading dates.
 *
 * Display timezone is fixed to IST (Asia/Kolkata) via a constant +5.5h
 * offset, independent of the browser timezone.
 */
(function () {
  'use strict';

  var LWC = window.LightweightCharts;
  // LWC v4.1.3: Year=0, Month=1, DayOfMonth=2, Time=3, TimeWithSeconds=4
  var TMT = (LWC && LWC.TickMarkType) || {
    Year: 0, Month: 1, DayOfMonth: 2, Time: 3, TimeWithSeconds: 4
  };

  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var IST_MS = 5.5 * 3600 * 1000;
  var INTRADAY = {
    '1m': 1, '3m': 1, '5m': 1, '10m': 1, '15m': 1, '30m': 1,
    '1h': 1, '2h': 1, '4h': 1
  };

  var CFG = new WeakMap();   // chart -> config
  var ST  = new WeakMap();   // chart -> render state { bars, last }

  function pad2(n) { n = n | 0; return n < 10 ? '0' + n : '' + n; }
  function isIntradayRange(r) { return !!INTRADAY[r]; }

  // epoch seconds -> IST calendar parts. Timezone-independent.
  function parts(epochSec) {
    if (epochSec == null || !isFinite(epochSec)) return null;
    var d = new Date(epochSec * 1000 + IST_MS);
    if (isNaN(d.getTime())) return null;
    return {
      y: d.getUTCFullYear(), moi: d.getUTCMonth(), d: d.getUTCDate(),
      hh: d.getUTCHours(), mm: d.getUTCMinutes()
    };
  }
  function dayKeyOf(p) { return p.y * 10000 + (p.moi + 1) * 100 + p.d; }
  function monKeyOf(p) { return p.y * 100 + (p.moi + 1); }

  // Accept number | {year,month,day} | 'YYYY-MM-DD' | numeric string -> epoch seconds.
  function toEpoch(t) {
    if (t == null) return null;
    if (typeof t === 'number') return isFinite(t) ? t : null;
    if (typeof t === 'object' && t.year) return Date.UTC(t.year, t.month - 1, t.day) / 1000;
    if (typeof t === 'string') {
      if (/^\d{4}-\d{2}-\d{2}$/.test(t)) {
        return Date.UTC(+t.slice(0, 4), +t.slice(5, 7) - 1, +t.slice(8, 10)) / 1000;
      }
      var n = Number(t);
      return isNaN(n) ? null : n;
    }
    return null;
  }

  function state(chart) {
    var s = ST.get(chart);
    if (!s) { s = { last: null }; ST.set(chart, s); }
    return s;
  }

  function onRangeChange(chart) {
    return function () {
      // Reset the render memo so the next pass re-anchors day/month/year
      // boundaries. The visible-bar count is read live (never cached) below.
      var s = state(chart);
      s.last = null;
    };
  }

  function defaultConfig() {
    return {
      getRange: function () { return window.activeRange || 'ALL'; },
      getBarTimes: function () { return window._intradayBarTimes || null; },
      getBarSecs: function () { return window._intradayBarSecs || null; },
      resolveEpoch: function (t) {
        var bt = window._intradayBarTimes, bs = window._intradayBarSecs;
        if (bt && bs && typeof t === 'number') {
          var i = Math.round(t / bs);
          return (i >= 0 && i < bt.length) ? bt[i] : null;
        }
        return toEpoch(t);
      }
    };
  }

  function register(chart, cfg) {
    if (!chart || !chart.timeScale) return;
    var base = defaultConfig();
    var c = cfg || {};
    c.getRange     = c.getRange     || base.getRange;
    c.getBarTimes  = c.getBarTimes  || base.getBarTimes;
    c.getBarSecs   = c.getBarSecs   || base.getBarSecs;
    c.resolveEpoch = c.resolveEpoch || base.resolveEpoch;
    CFG.set(chart, c);
    state(chart);
    try { chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange(chart)); } catch (e) {}
  }

  function reset(chart) {
    if (!chart) return;
    var s = ST.get(chart);
    if (s) s.last = null;
  }

  function barsOf(chart) {
    // Read the CURRENT visible range every call. A cached value could be stale
    // during initial load (empty/fitted range) and would wrongly push the
    // formatter into the date-only tier until the next range event.
    try {
      var lr = chart.timeScale().getVisibleLogicalRange();
      if (lr && lr.to != null && lr.from != null) {
        return Math.max(1, Math.round(lr.to - lr.from));
      }
    } catch (e) {}
    return 75;
  }

  function widthOf(chart) {
    try { var w = chart.timeScale().width(); return w > 0 ? w : 0; } catch (e) { return 0; }
  }

  // ── intraday (5m/15m/30m/1h/4h and raw-epoch intraday) ──────────────────
  function fmtIntraday(o) {
    var p = o.p;
    var time = pad2(p.hh) + ':' + pad2(p.mm);
    var day  = pad2(p.d) + ' ' + MON[p.moi];
    var mon  = MON[p.moi];
    var yr   = '' + p.y;
    var narrow = o.width > 0 && o.width < 520;

    if (o.mt === TMT.Year)  return yr;
    if (o.mt === TMT.Month) return p.moi === 0 ? (mon + ' ' + yr) : mon;

    // Genuine trading-day boundary (real bar sequence / render memo).
    if (o.newDay) {
      var dayY = o.newYr ? (day + ' ' + yr) : day;
      // Close zoom keeps the opening time next to the date.
      if (o.bars <= 130 && !narrow) return dayY + ' ' + time;
      return dayY; // "08 Oct" (or "01 Jan 2027" at a year change)
    }
    // Normal zoom: concise time labels between day boundaries.
    if (o.bars <= 1500) return time;
    // Zoomed out: dates, escalating to month/year on transitions.
    if (o.newMon) return p.moi === 0 ? (mon + ' ' + yr) : mon;
    return day;
  }

  // ── daily / weekly / monthly (business-day times) ───────────────────────
  function fmtDay(o) {
    var p = o.p;
    var mon = MON[p.moi];
    var yr  = '' + p.y;
    if (o.mt === TMT.Year)  return yr;
    if (o.mt === TMT.Month) return (p.moi === 0 || o.newYr) ? (mon + ' ' + yr) : mon;
    if (o.bars <= 40)  return '' + p.d;             // close zoom: day number
    if (o.bars <= 300) return pad2(p.d) + ' ' + mon; // normal: "08 Oct"
    return mon;                                      // zoomed out: month
  }

  function tick(chart, time, markType, rangeOverride) {
    if (!chart || !chart.timeScale) return '';
    var cfg = CFG.get(chart) || defaultConfig();
    var range = rangeOverride || cfg.getRange();
    var intraday = isIntradayRange(range);

    var idx = null, epoch = null, prevP = null;

    if (intraday) {
      var bt = cfg.getBarTimes();
      var bs = cfg.getBarSecs();
      if (bt && bs) {
        idx = Math.round(Number(time) / bs);
        // Future whitespace / out-of-range: do not fabricate a session label.
        if (!(idx >= 0 && idx < bt.length)) return '';
        epoch = bt[idx];
        if (idx > 0) prevP = parts(bt[idx - 1]);
      } else {
        epoch = cfg.resolveEpoch(time);       // raw-epoch intraday (mini, market)
      }
    } else {
      epoch = cfg.resolveEpoch(time);         // business-day
    }
    if (epoch == null) return '';
    var p = parts(epoch);
    if (!p) return '';

    var s = state(chart);
    var bars = barsOf(chart);
    var width = widthOf(chart);

    var last = s.last;
    // A backwards jump means LWC started a new render pass; re-anchor.
    if (last && idx != null && last.idx != null && idx < last.idx) { last = null; s.last = null; }

    var newDay = !last, newMon = false, newYr = false;
    if (last) {
      newDay = dayKeyOf(p) !== last.dayKey;
      newMon = monKeyOf(p) !== last.monKey;
      newYr  = p.y !== last.y;
    }
    // Definitive boundary from the real bar sequence (previous real bar).
    if (prevP && dayKeyOf(p) !== dayKeyOf(prevP)) newDay = true;

    if (!last || idx == null || last.idx == null || idx >= last.idx) {
      s.last = { idx: idx, dayKey: dayKeyOf(p), monKey: monKeyOf(p), y: p.y };
    }

    var o = { p: p, mt: markType, bars: bars, width: width,
              newDay: newDay, newMon: newMon, newYr: newYr };
    return intraday ? fmtIntraday(o) : fmtDay(o);
  }

  window.LeverageAxis = {
    version: '1.0.0',
    TMT: TMT,
    register: register,
    reset: reset,
    tick: tick,
    parts: parts,
    toEpoch: toEpoch,
    isIntradayRange: isIntradayRange
  };
})();
