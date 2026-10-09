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
  var BAR_MIN = {
    '1m': 1, '3m': 3, '5m': 5, '10m': 10, '15m': 15, '30m': 30,
    '1h': 60, '2h': 120, '4h': 240
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
    if (!s) { s = { last: null, seen: { d: {}, m: 0, y: 0 }, lastCall: 0, frameArmed: false, renderIdxs: null, rangeKey: null }; ST.set(chart, s); }
    return s;
  }

  // Reset the per-render "already labelled" sets on the next animation frame.
  // LWC lays out tick marks within a single frame, so every formatter call for
  // one render shares these sets (giving one date per day) and they are cleared
  // before the next render -- which a fixed time gap cannot guarantee when two
  // renders happen back to back.
  function armFrameReset(s) {
    if (s.frameArmed) return;
    s.frameArmed = true;
    var clear = function () { s.seen = { d: {}, m: 0, y: 0 }; s.frameArmed = false; };
    try {
      (window.requestAnimationFrame || function (f) { return setTimeout(f, 16); })(clear);
    } catch (e) { clear(); }
  }

  function onRangeChange(chart) {
    return function () {
      // Reset the render memo + labelled sets so the next pass re-anchors
      // day/month/year boundaries. The visible-bar count is read live below.
      var s = state(chart);
      // Only the render memo is reset here; the "already labelled" sets are
      // reset by the visible-range key check inside tick(), which cannot fire
      // mid-render.
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
    if (s) { s.last = null; s.seen = { d: {}, m: 0, y: 0 }; s.renderIdxs = null; }
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
  // Tier is driven by the number of VISIBLE TRADING DAYS (bars / bars-per-day),
  // matching TradingView: times only while ~<2 sessions are visible; beyond that
  // one date per trading day (month/year on calendar transitions).
  function fmtIntraday(o) {
    var p = o.p;
    var time = pad2(p.hh) + ':' + pad2(p.mm);
    var day  = pad2(p.d) + ' ' + MON[p.moi];
    var mon  = MON[p.moi];
    var yr   = '' + p.y;
    var seen = o.s.seen;

    // Year / Month marks from Lightweight Charts (wide zoom): once per distinct
    // year/month, tracked in the persistent `seen` sets.
    if (o.mt === TMT.Year)  { if (seen.y !== p.y) { seen.y = p.y; return yr; } return ''; }
    if (o.mt === TMT.Month) { var mk = monKeyOf(p); if (seen.m !== mk) { seen.m = mk; return p.moi === 0 ? (mon + ' ' + yr) : mon; } return ''; }

    var barMin = BAR_MIN[o.range] || 5;
    var barsPerDay = Math.max(1, Math.floor(375 / barMin));
    var spanDays = o.bars / barsPerDay;
    var mins = p.hh * 60 + p.mm;
    var sessionStart = mins >= (9 * 60 + 15) && mins <= (9 * 60 + 15 + barMin - 1);

    if (spanDays <= 1.8) {
      // Time tier. A single-session view shows times only (no date). When the
      // visible range spans a day boundary, the date is shown exactly once per
      // trading day, on the first tick that lands within one tick-span of the
      // day's first bar (Lightweight Charts never lands a tick on the exact
      // 09:15 bar). This is a pure function of the tick's real bar index and the
      // day's first bar, so the same day's date can never be emitted twice --
      // regardless of call order, panning, or how a render is split.
      if (!o.multiDay) return time;
      var dkt = dayKeyOf(p);
      var _dkey = p.y + '-' + pad2(p.moi + 1) + '-' + pad2(p.d);
      var d0 = (window._intradayDayFirstBarMap && o.idx != null)
        ? window._intradayDayFirstBarMap[_dkey] : null;
      if (d0 != null) {
        var _step = Math.max(1, Math.round(o.bars / 4));
        var _into = o.idx - d0;
        if (_into >= 0 && _into < _step) return o.realYearBoundary ? (day + ' ' + yr) : day;
        return time;
      }
      // Fallback for raw-epoch charts without the bar map: one date per day.
      if ((sessionStart || o.realDayBoundary === true || o.newDay === true) && !seen.d[dkt]) {
        seen.d[dkt] = 1;
        return o.realYearBoundary ? (day + ' ' + yr) : day;
      }
      return time;
    }

    // Date tier: one date per trading day, month/year on genuine calendar
    // transitions. Same deterministic first-tick-of-day placement when the bar
    // map is available.
    var dk = dayKeyOf(p);
    if (o.realYearBoundary  && seen.y !== p.y)  { seen.y = p.y; seen.d[dk] = 1; return yr; }
    if (o.realMonthBoundary && seen.m !== monKeyOf(p)) { seen.m = monKeyOf(p); seen.d[dk] = 1; return p.moi === 0 ? (mon + ' ' + yr) : mon; }
    var _dkey2 = p.y + '-' + pad2(p.moi + 1) + '-' + pad2(p.d);
    var d02 = (window._intradayDayFirstBarMap && o.idx != null)
      ? window._intradayDayFirstBarMap[_dkey2] : null;
    if (d02 != null) {
      var _step2 = Math.max(1, Math.round(o.bars / 4));
      var _into2 = o.idx - d02;
      return (_into2 >= 0 && _into2 < _step2) ? day : '';
    }
    if (seen.d[dk]) return '';
    seen.d[dk] = 1;
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
    // ── Reset the "already labelled" sets when the VISIBLE RANGE changes ──
    // A range/zoom/pan change re-invokes the formatter; a plain candle repaint
    // does not (verified). The visible range is constant for every tick within
    // one render, so keying the reset on it cannot fire mid-render (which is
    // what previously let the same day be dated twice) yet still re-emits the
    // dates on every zoom.
    var _lr = null;
    try { _lr = chart.timeScale().getVisibleLogicalRange(); } catch (e) {}
    var _rk = _lr ? (Math.round(_lr.from * 100) + ':' + Math.round(_lr.to * 100)) : null;
    if (_rk !== s.rangeKey) {
      s.rangeKey = _rk;
      s.seen = { d: {}, m: 0, y: 0 };
    }
    // Does the visible range span more than one trading day? Date labels are only
    // meaningful then -- a single-session view (all visible bars on one trading
    // day) must show times only, with no date at all.
    var multiDay = false;
    try {
      var _vr = chart.timeScale().getVisibleRange();
      if (_vr && _vr.from != null && _vr.to != null) {
        var _eF = null, _eT = null;
        if (intraday && idx != null) {
          var _bt = cfg.getBarTimes(), _bs = cfg.getBarSecs();
          if (_bt && _bs) {
            var _iF = Math.max(0, Math.min(_bt.length - 1, Math.round(Number(_vr.from) / _bs)));
            var _iT = Math.max(0, Math.min(_bt.length - 1, Math.round(Number(_vr.to) / _bs)));
            _eF = _bt[_iF]; _eT = _bt[_iT];
          }
        }
        if (_eF == null) { _eF = toEpoch(_vr.from); _eT = toEpoch(_vr.to); }
        var _pF = parts(_eF), _pT = parts(_eT);
        if (_pF && _pT) multiDay = dayKeyOf(_pF) !== dayKeyOf(_pT);
      }
    } catch (e) {}
    var bars = barsOf(chart);
    var width = widthOf(chart);

    var last = s.last;
    // A backwards jump means LWC started a new render pass; re-anchor.
    if (last && idx != null && last.idx != null && idx < last.idx) { last = null; s.last = null; }

    var first = !last;
    var newDay = first, newMon = false, newYr = false;
    if (last) {
      newDay = dayKeyOf(p) !== last.dayKey;
      newMon = monKeyOf(p) !== last.monKey;
      newYr  = p.y !== last.y;
    }
    // Definitive boundary from the real bar sequence (previous real bar).
    var realDayBoundary = prevP ? (dayKeyOf(p) !== dayKeyOf(prevP)) : null;
    var realMonthBoundary = prevP ? (prevP.y === p.y && prevP.moi !== p.moi) : null;
    var realYearBoundary = prevP ? (prevP.y !== p.y) : null;
    if (realDayBoundary) newDay = true;

    if (!last || idx == null || last.idx == null || idx >= last.idx) {
      s.last = { idx: idx, dayKey: dayKeyOf(p), monKey: monKeyOf(p), y: p.y };
    }

    // "Already labelled" sets, reset only when the visible range changes (see
    // onRangeChange). They persist across re-renders so a given trading day can
    // never be dated twice, no matter what order Lightweight Charts calls the
    // formatter in or how a render is split across frames.
    s.lastCall = (window.performance && window.performance.now) ? window.performance.now() : Date.now();

    var o = { p: p, mt: markType, bars: bars, width: width, range: range, s: s, idx: idx,
              first: first, realDayBoundary: realDayBoundary,
              realMonthBoundary: realMonthBoundary, realYearBoundary: realYearBoundary,
              newDay: newDay, newMon: newMon, newYr: newYr, multiDay: multiDay };
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
